"""Unit tests for the offline SentenceTransformer embedding adapter."""

import importlib.util
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path
from threading import Event, Lock, Thread
from time import sleep
from types import SimpleNamespace
from typing import Any

import pytest

from omf_retrieval.infrastructure.embedding.provider import EmbeddingProvider
from omf_retrieval.settings import Settings

DIMENSION = 1024


def _unit_vector(seed: int = 0) -> tuple[float, ...]:
    values = [0.0] * DIMENSION
    values[seed % DIMENSION] = 1.0
    return tuple(values)


class _RecordingModel:
    """Mirror the SentenceTransformer encode boundary without loading a model."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def encode(self, inputs: list[str], **kwargs: Any) -> tuple[tuple[float, ...], ...]:
        self.calls.append((tuple(inputs), dict(kwargs)))
        return tuple(
            _unit_vector(int(value) if value.isascii() and value.isdecimal() else index)
            for index, value in enumerate(inputs)
        )


class _RecordingTokenizer:
    """Mirror the complete tokenization fields consumed by the adapter."""

    is_fast = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, text: str, **kwargs: Any) -> dict[str, object]:
        self.calls.append((text, dict(kwargs)))
        return {
            "input_ids": [ord(character) for character in text],
            "offset_mapping": [(index, index + 1) for index in range(len(text))],
        }


def _install_backend(
    monkeypatch: Any,
    module: Any,
    *,
    model: _RecordingModel | None = None,
    tokenizer: _RecordingTokenizer | None = None,
) -> tuple[list[object], _RecordingModel, _RecordingTokenizer]:
    loader_calls: list[object] = []
    fake_model = model or _RecordingModel()
    fake_tokenizer = tokenizer or _RecordingTokenizer()

    def loader(identity: object) -> object:
        loader_calls.append(identity)
        return SimpleNamespace(
            model=fake_model,
            tokenizer=fake_tokenizer,
            tokenizer_version="5.15.0",
        )

    monkeypatch.setattr(module, "_RUNTIME_REGISTRY", {}, raising=False)
    monkeypatch.setattr(module, "_load_runtime_backend", loader, raising=False)
    return loader_calls, fake_model, fake_tokenizer


def _settings(*, cache_dir: Path | None = None) -> Settings:
    return Settings(
        environment="test",
        embedding_cache_dir=cache_dir,
        embedding_device="cpu",
    )


def _provider_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "embedding_device": "cpu",
        "query_instruction": "PREFIX<{query}>",
    }
    values.update(overrides)
    return Settings(**values)


def test_sentence_transformer_adapter_module_exists() -> None:
    """Removing the concrete adapter makes the approved runtime unavailable."""
    module_spec = importlib.util.find_spec(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )

    assert module_spec is not None


def test_package_exports_concrete_provider_and_token_counter() -> None:
    """Dropping either adapter export breaks runtime composition."""
    embedding = import_module("omf_retrieval.infrastructure.embedding")

    assert getattr(embedding, "SentenceTransformerEmbeddingProvider", None) is not None
    assert getattr(embedding, "SentenceTransformerTokenCounter", None) is not None


def test_provider_and_counter_share_one_lazy_runtime(monkeypatch: Any) -> None:
    """Eager or duplicate loads waste process memory and can reach the network."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    loader_calls, _, _ = _install_backend(monkeypatch, module)
    provider = module.SentenceTransformerEmbeddingProvider(_settings())
    counter = module.SentenceTransformerTokenCounter(_settings())

    assert isinstance(provider, EmbeddingProvider)
    assert loader_calls == []
    assert provider.is_ready() is False
    assert counter.descriptor.add_special_tokens is False

    vector = None
    tokens = None
    try:
        vector = provider.embed_documents(("A",))[0]
        tokens = counter.encode("가B")
    except RuntimeError:
        pass

    assert vector == _unit_vector()
    assert tokens == (44032, 66)
    assert len(loader_calls) == 1
    assert provider.is_ready() is False


def test_readiness_uses_frozen_manifest_identity_without_loading(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """Readiness is cache integrity, not whether a fake backend was loaded."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    loader_calls, _, _ = _install_backend(monkeypatch, module)
    checks: list[tuple[Path | None, str, str]] = []

    def verify(cache: Path | None, *, model_name: str, revision: str) -> bool:
        checks.append((cache, model_name, revision))
        return True

    monkeypatch.setattr(module, "verify_model_manifest", verify)
    settings = _provider_settings(embedding_cache_dir=tmp_path)
    provider = module.SentenceTransformerEmbeddingProvider(settings)
    settings.embedding_cache_dir = Path("changed")
    settings.embedding_model_name = "changed"
    settings.embedding_model_revision = "changed"

    assert provider.is_ready() is True
    assert loader_calls == []
    assert checks == [
        (
            tmp_path,
            "Qwen/Qwen3-Embedding-0.6B",
            "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        )
    ]


def test_readiness_propagates_custom_base_exception_identity(monkeypatch: Any) -> None:
    class StopReadiness(BaseException):
        pass

    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    failure = StopReadiness("same-object")

    def fail(*args: object, **kwargs: object) -> bool:
        raise failure

    monkeypatch.setattr(module, "verify_model_manifest", fail)
    provider = module.SentenceTransformerEmbeddingProvider(_provider_settings())
    with pytest.raises(StopReadiness) as captured:
        provider.is_ready()
    assert captured.value is failure


def test_adapter_descriptors_snapshot_settings_at_construction(
    monkeypatch: Any,
) -> None:
    """Mutable Settings must not split public descriptors from runtime identity."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    _install_backend(monkeypatch, module)
    settings = _provider_settings()
    provider = module.SentenceTransformerEmbeddingProvider(settings)
    counter = module.SentenceTransformerTokenCounter(settings)
    provider_descriptor = provider.descriptor
    provider_config = provider.embedding_config_snapshot
    counter_descriptor = counter.descriptor

    settings.embedding_model_name = "changed/model"
    settings.embedding_model_revision = "changed-revision"
    settings.embedding_dimension = 2
    settings.embedding_cache_dir = Path("changed-cache")

    assert provider.descriptor == provider_descriptor
    assert provider.embedding_config_snapshot == provider_config
    assert provider_config.descriptor == provider_descriptor
    assert provider_config.as_config()["query"] == {  # type: ignore[index]
        "instruction": provider_config.query_instruction
    }
    assert counter.descriptor == counter_descriptor


def test_provider_behavior_snapshots_instruction_and_batch_size(
    monkeypatch: Any,
) -> None:
    """A caller mutating Settings later must not alter an existing adapter."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    loader_calls, model, _ = _install_backend(monkeypatch, module)
    settings = _provider_settings(
        embedding_batch_size=2,
        query_instruction="ORIGINAL<{query}>",
    )
    provider = module.SentenceTransformerEmbeddingProvider(settings)

    settings.embedding_batch_size = 1
    settings.query_instruction = "CHANGED<{query}>"
    settings.embedding_model_name = "changed/model"
    settings.embedding_model_revision = "changed-revision"
    settings.embedding_cache_dir = Path("changed-cache")

    provider.embed_query("query")
    provider.embed_documents(("0", "1", "2"))

    assert [inputs for inputs, _ in model.calls] == [
        ("ORIGINAL<query>",),
        ("0", "1"),
        ("2",),
    ]
    assert all(kwargs["batch_size"] == 2 for _, kwargs in model.calls)
    assert len(loader_calls) == 1
    identity = loader_calls[0]
    assert identity.model_name == "Qwen/Qwen3-Embedding-0.6B"
    assert identity.revision == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    assert identity.cache_dir is None


@pytest.mark.parametrize("template", ["no placeholder", "{query} then {query}"])
def test_provider_rejects_query_instruction_without_exactly_one_placeholder(
    template: str,
) -> None:
    """Missing or duplicate placeholders violate exactly-once query handling."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    settings = _provider_settings(query_instruction=template)

    with pytest.raises(ValueError, match="Invalid query instruction"):
        module.SentenceTransformerEmbeddingProvider(settings)


def test_concurrent_first_use_loads_one_backend_per_identity(monkeypatch: Any) -> None:
    """An unlocked first-use race can load duplicate GPU models in one process."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    loader_identities: list[object] = []
    call_lock = Lock()
    model = _RecordingModel()
    tokenizer = _RecordingTokenizer()

    def slow_loader(identity: object) -> object:
        with call_lock:
            loader_identities.append(identity)
        sleep(0.03)
        return SimpleNamespace(
            model=model,
            tokenizer=tokenizer,
            tokenizer_version="5.15.0",
        )

    monkeypatch.setattr(module, "_RUNTIME_REGISTRY", {})
    monkeypatch.setattr(module, "_load_runtime_backend", slow_loader)
    provider = module.SentenceTransformerEmbeddingProvider(_settings())
    counter = module.SentenceTransformerTokenCounter(_settings())

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(
                provider.embed_documents if index % 2 == 0 else counter.encode,
                ("문서",) if index % 2 == 0 else "문서",
            )
            for index in range(8)
        ]
        results = [future.result() for future in futures]

    assert len(loader_identities) == 1
    assert results[0] == (_unit_vector(),)
    assert results[1] == (47928, 49436)

    isolated = module.SentenceTransformerEmbeddingProvider(
        _settings(cache_dir=Path("separate-cache"))
    )
    assert isolated.embed_documents(("별도",)) == (_unit_vector(),)
    assert len(loader_identities) == 2


def test_query_instruction_is_applied_once_and_documents_are_unchanged(
    monkeypatch: Any,
) -> None:
    """Missing or duplicated query instruction changes retrieval embeddings."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    _, model, _ = _install_backend(monkeypatch, module)
    provider = module.SentenceTransformerEmbeddingProvider(_provider_settings())

    provider.embed_query("승인 정책")
    provider.embed_documents(("승인 정책",))

    assert [inputs for inputs, _ in model.calls] == [
        ("PREFIX<승인 정책>",),
        ("승인 정책",),
    ]
    assert all(
        kwargs
        == {
            "batch_size": 32,
            "show_progress_bar": False,
            "convert_to_numpy": True,
            "convert_to_tensor": False,
            "normalize_embeddings": True,
        }
        for _, kwargs in model.calls
    )


def test_documents_are_chunked_by_batch_size_with_stable_immutable_order(
    monkeypatch: Any,
) -> None:
    """One oversized encode call or reordered batches corrupts stored vectors."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    loader_calls, model, _ = _install_backend(monkeypatch, module)
    provider = module.SentenceTransformerEmbeddingProvider(
        _provider_settings(embedding_batch_size=32)
    )
    documents = tuple(str(index) for index in range(65))

    result = provider.embed_documents(documents)

    assert [len(inputs) for inputs, _ in model.calls] == [32, 32, 1]
    assert tuple(inputs for inputs, _ in model.calls) == (
        documents[:32],
        documents[32:64],
        documents[64:],
    )
    assert type(result) is tuple
    assert all(type(vector) is tuple for vector in result)
    assert result == tuple(_unit_vector(index) for index in range(65))
    assert len(loader_calls) == 1


def test_empty_documents_and_text_do_not_load_backend(monkeypatch: Any) -> None:
    """Empty work should not initialize a large model or tokenizer."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    loader_calls, _, _ = _install_backend(monkeypatch, module)
    provider = module.SentenceTransformerEmbeddingProvider(_provider_settings())
    counter = module.SentenceTransformerTokenCounter(_provider_settings())

    assert provider.embed_documents(()) == ()
    assert counter.encode("") == ()
    assert counter.offsets("") == ()
    assert loader_calls == []
    assert provider.is_ready() is False


@pytest.mark.parametrize("environment", ["development", "test"])
@pytest.mark.parametrize("device", ["cuda:0", "mps"])
def test_non_production_adapter_rejects_non_cpu_devices(
    environment: str, device: str
) -> None:
    """Development and test adapters must not silently allocate accelerators."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    settings = Settings(environment=environment, embedding_device=device)

    with pytest.raises(ValueError, match="Invalid embedding device"):
        module.SentenceTransformerEmbeddingProvider(settings)


@pytest.mark.parametrize("invalid_query", ["", 7, True])
def test_provider_rejects_invalid_queries_before_loading(
    monkeypatch: Any, invalid_query: object
) -> None:
    """Empty or non-string queries must not become instructed model input."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    loader_calls, _, _ = _install_backend(monkeypatch, module)
    provider = module.SentenceTransformerEmbeddingProvider(_provider_settings())

    with pytest.raises(ValueError, match="Invalid embedding input"):
        provider.embed_query(invalid_query)

    assert loader_calls == []


@pytest.mark.parametrize(
    "invalid_documents",
    ["문서", ("",), ("정상", 7), (True,)],
)
def test_provider_rejects_invalid_document_sequences_before_loading(
    monkeypatch: Any, invalid_documents: object
) -> None:
    """Strings-as-sequences and invalid entries must fail before model load."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    loader_calls, _, _ = _install_backend(monkeypatch, module)
    provider = module.SentenceTransformerEmbeddingProvider(_provider_settings())

    with pytest.raises(ValueError, match="Invalid embedding input"):
        provider.embed_documents(invalid_documents)

    assert loader_calls == []


def test_backend_exception_is_sanitized_without_exception_chaining(
    monkeypatch: Any,
) -> None:
    """Backend exception text and chaining can disclose source or credentials."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    secret = "sensitive://model-token-and-source"

    class RaisingModel:
        def encode(self, inputs: list[str], **kwargs: Any) -> object:
            raise OSError(secret)

    monkeypatch.setattr(module, "_RUNTIME_REGISTRY", {})
    monkeypatch.setattr(
        module,
        "_load_runtime_backend",
        lambda identity: SimpleNamespace(
            model=RaisingModel(),
            tokenizer=_RecordingTokenizer(),
            tokenizer_version="5.15.0",
        ),
    )
    provider = module.SentenceTransformerEmbeddingProvider(_provider_settings())

    with pytest.raises(RuntimeError, match="Embedding backend unavailable") as caught:
        provider.embed_documents(("private source",))

    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_token_counter_returns_source_backed_ids_offsets_and_descriptor(
    monkeypatch: Any,
) -> None:
    """Decode reconstruction or special tokens would break source line boundaries."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    _, _, tokenizer = _install_backend(monkeypatch, module)
    counter = module.SentenceTransformerTokenCounter(_provider_settings())
    text = "A한\n🙂"

    token_ids = counter.encode(text)
    offsets = counter.offsets(text)

    assert token_ids == (65, 54620, 10, 128578)
    assert offsets == ((0, 1), (1, 2), (2, 3), (3, 4))
    assert len(token_ids) == len(offsets)
    assert [text[start:end] for start, end in offsets] == ["A", "한", "\n", "🙂"]
    assert [kwargs for _, kwargs in tokenizer.calls] == [
        {
            "add_special_tokens": False,
            "return_offsets_mapping": True,
            "return_attention_mask": False,
            "return_token_type_ids": False,
        },
        {
            "add_special_tokens": False,
            "return_offsets_mapping": True,
            "return_attention_mask": False,
            "return_token_type_ids": False,
        },
    ]
    assert counter.descriptor == counter.descriptor
    assert counter.descriptor.model_name == "Qwen/Qwen3-Embedding-0.6B"
    assert counter.descriptor.revision == ("97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3")
    assert counter.descriptor.library_name == "transformers"
    assert counter.descriptor.library_version == "5.15.0"
    assert counter.descriptor.add_special_tokens is False


def test_concurrent_model_calls_use_one_bounded_inference_lane(
    monkeypatch: Any,
) -> None:
    """Concurrent model entry can overcommit the single approved GPU."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )

    class SerialProbeModel:
        def __init__(self) -> None:
            self.lock = Lock()
            self.active = 0
            self.maximum_active = 0

        def encode(
            self, inputs: list[str], **kwargs: Any
        ) -> tuple[tuple[float, ...], ...]:
            with self.lock:
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
            sleep(0.02)
            with self.lock:
                self.active -= 1
            return tuple(_unit_vector() for _ in inputs)

    model = SerialProbeModel()
    monkeypatch.setattr(module, "_RUNTIME_REGISTRY", {})
    monkeypatch.setattr(
        module,
        "_load_runtime_backend",
        lambda identity: SimpleNamespace(
            model=model,
            tokenizer=_RecordingTokenizer(),
            tokenizer_version="5.15.0",
        ),
    )
    provider = module.SentenceTransformerEmbeddingProvider(_provider_settings())

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda index: provider.embed_documents((str(index),)),
                range(8),
            )
        )

    assert model.maximum_active == 1
    assert results == [(_unit_vector(),)] * 8


def test_reentrant_model_call_fails_before_second_model_entry(monkeypatch: Any) -> None:
    """Same-thread reentry must fail instead of recursing or deadlocking."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )

    class ReentrantModel:
        def __init__(self) -> None:
            self.calls = 0
            self.provider: object | None = None

        def encode(self, inputs: list[str], **kwargs: Any) -> object:
            self.calls += 1
            if self.calls > 2:
                raise RuntimeError("recursive model entry")
            assert self.provider is not None
            return self.provider.embed_documents(("nested",))

    model = ReentrantModel()
    monkeypatch.setattr(module, "_RUNTIME_REGISTRY", {})
    monkeypatch.setattr(
        module,
        "_load_runtime_backend",
        lambda identity: SimpleNamespace(
            model=model,
            tokenizer=_RecordingTokenizer(),
            tokenizer_version="5.15.0",
        ),
    )
    provider = module.SentenceTransformerEmbeddingProvider(_provider_settings())
    model.provider = provider

    with pytest.raises(RuntimeError, match="Embedding backend unavailable"):
        provider.embed_documents(("outer",))

    assert model.calls == 1


def test_default_loader_uses_pinned_offline_non_remote_kwargs(monkeypatch: Any) -> None:
    """A missing pin or local-only flag can execute or download unapproved code."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    model_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    shared_tokenizer = SimpleNamespace(is_fast=True)

    class FakeSentenceTransformer:
        def __init__(self, *args: object, **kwargs: object) -> None:
            model_calls.append((args, dict(kwargs)))
            self.tokenizer = shared_tokenizer

    class FakeAutoTokenizer:
        @classmethod
        def from_pretrained(cls, *args: object, **kwargs: object) -> object:
            raise AssertionError("the model tokenizer must be reused")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoTokenizer=FakeAutoTokenizer),
    )
    monkeypatch.setattr(
        module,
        "verified_model_snapshot",
        lambda *args, **kwargs: Path("/model-cache/prepared"),
    )
    identity = module._RuntimeIdentity(
        model_name="Qwen/Qwen3-Embedding-0.6B",
        revision="fixed-revision",
        device="cuda:0",
        cache_dir="/model-cache",
    )

    backend = module._load_runtime_backend(identity)

    assert model_calls == [
        (
            ("/model-cache/prepared",),
            {
                "device": "cuda:0",
                "cache_folder": "/model-cache",
                "trust_remote_code": False,
                "revision": "fixed-revision",
                "local_files_only": True,
                "model_kwargs": {"attn_implementation": "sdpa"},
                "processor_kwargs": {"use_fast": True},
            },
        )
    ]
    assert backend.tokenizer is shared_tokenizer
    assert backend.tokenizer_version == "5.15.0"


def test_import_and_construction_do_not_import_heavy_libraries() -> None:
    """Adapter import or construction must not load Torch/model libraries."""
    script = """
import sys
from omf_retrieval.infrastructure.embedding import (
    SentenceTransformerEmbeddingProvider,
    SentenceTransformerTokenCounter,
)
from omf_retrieval.settings import Settings

settings = Settings(environment="test", embedding_device="cpu")
provider = SentenceTransformerEmbeddingProvider(settings)
counter = SentenceTransformerTokenCounter(settings)
assert provider.is_ready() is False
assert counter.descriptor.add_special_tokens is False
assert "sentence_transformers" not in sys.modules
assert "transformers" not in sys.modules
assert "torch" not in sys.modules
print("lazy")
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "lazy\n"


def test_slow_loader_does_not_hold_global_registry_lock(monkeypatch: Any) -> None:
    """One slow identity load must not block construction of another identity."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    load_started = Event()
    release_load = Event()
    construction_finished = Event()
    failures: list[BaseException] = []

    def loader(identity: object) -> object:
        load_started.set()
        release_load.wait(timeout=1)
        return SimpleNamespace(
            model=_RecordingModel(),
            tokenizer=_RecordingTokenizer(),
            tokenizer_version="5.15.0",
        )

    monkeypatch.setattr(module, "_RUNTIME_REGISTRY", {})
    monkeypatch.setattr(module, "_load_runtime_backend", loader)
    first = module.SentenceTransformerEmbeddingProvider(_provider_settings())

    def load_first() -> None:
        try:
            first.embed_documents(("first",))
        except BaseException as error:
            failures.append(error)

    def construct_second() -> None:
        try:
            module.SentenceTransformerEmbeddingProvider(
                _provider_settings(embedding_cache_dir=Path("second-cache"))
            )
        except BaseException as error:
            failures.append(error)
        finally:
            construction_finished.set()

    loader_thread = Thread(target=load_first, daemon=True)
    loader_thread.start()
    assert load_started.wait(timeout=1)
    constructor_thread = Thread(target=construct_second, daemon=True)
    constructor_thread.start()
    constructed_without_loader_release = construction_finished.wait(timeout=0.2)
    release_load.set()
    loader_thread.join(timeout=1)
    constructor_thread.join(timeout=1)

    assert constructed_without_loader_release is True
    assert failures == []
