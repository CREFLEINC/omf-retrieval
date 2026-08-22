"""Adversarial tests for SentenceTransformer embedding boundaries."""

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from math import inf, nan
from threading import Barrier, Event, Lock
from time import sleep
from types import SimpleNamespace
from typing import Any

import pytest

from omf_retrieval.domain.errors import DomainError
from omf_retrieval.settings import Settings

DIMENSION = 1024


class _LoaderAbort(BaseException):
    """Exercise non-standard process-control signals at the loader boundary."""


def _settings() -> Settings:
    return Settings(environment="test", embedding_device="cpu")


def _valid_vector() -> tuple[float, ...]:
    return (1.0,) + (0.0,) * (DIMENSION - 1)


class _OutputModel:
    def __init__(self, output: object) -> None:
        self.output = output

    def encode(self, inputs: list[str], **kwargs: Any) -> object:
        return self.output


def _provider(monkeypatch: Any, model: object) -> object:
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    monkeypatch.setattr(module, "_RUNTIME_REGISTRY", {})
    monkeypatch.setattr(
        module,
        "_load_runtime_backend",
        lambda identity: SimpleNamespace(
            model=model,
            tokenizer=object(),
            tokenizer_version="5.15.0",
        ),
    )
    return module.SentenceTransformerEmbeddingProvider(_settings())


@pytest.mark.parametrize(
    "output",
    [
        (),
        ((_valid_vector()), (_valid_vector())),
        ((1.0,) + (0.0,) * (DIMENSION - 2),),
        (((nan,) + (0.0,) * (DIMENSION - 1)),),
        (((inf,) + (0.0,) * (DIMENSION - 1)),),
        (((0.0,) * DIMENSION),),
        (((True,) + (0.0,) * (DIMENSION - 1)),),
        ((("not-a-number",) + (0.0,) * (DIMENSION - 1)),),
    ],
)
def test_malformed_embedding_outputs_fail_closed(
    monkeypatch: Any, output: object
) -> None:
    """Wrong count, shape, values, or norm must never reach vector storage."""
    provider = _provider(monkeypatch, _OutputModel(output))

    with pytest.raises(
        ValueError, match="Embedding backend returned malformed vectors"
    ) as caught:
        provider.embed_documents(("document",))

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert type(caught.value) is DomainError


def test_numeric_conversion_failure_is_sanitized_without_secret_context(
    monkeypatch: Any,
) -> None:
    """A hostile numeric object must not leak its conversion exception."""
    secret = "secret-from-float-conversion"

    class RaisingFloat(float):
        def __float__(self) -> float:
            raise RuntimeError(secret)

    output = (((RaisingFloat(1.0),) + (0.0,) * (DIMENSION - 1)),)
    provider = _provider(monkeypatch, _OutputModel(output))

    with pytest.raises(
        ValueError, match="Embedding backend returned malformed vectors"
    ) as caught:
        provider.embed_documents(("document",))

    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert type(caught.value) is DomainError


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_embedding_backend_control_flow_exceptions_propagate(
    monkeypatch: Any, exception_type: type[BaseException]
) -> None:
    """Process control exceptions must not be converted into readiness failures."""

    class RaisingModel:
        def encode(self, inputs: list[str], **kwargs: Any) -> object:
            raise exception_type("control-flow")

    provider = _provider(monkeypatch, RaisingModel())

    with pytest.raises(exception_type, match="control-flow"):
        provider.embed_documents(("document",))


def test_malicious_document_sequence_failure_is_source_free(
    monkeypatch: Any,
) -> None:
    """Sequence protocol failures must not reveal caller-controlled text."""
    secret = "secret-from-document-sequence"

    class RaisingSequence:
        def __len__(self) -> int:
            raise RuntimeError(secret)

        def __getitem__(self, index: int) -> str:
            raise RuntimeError(secret)

    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    provider = module.SentenceTransformerEmbeddingProvider(_settings())

    with pytest.raises(ValueError, match="Invalid embedding input") as caught:
        provider.embed_documents(RaisingSequence())

    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_outer_embedding_sequence_materialization_is_bounded(
    monkeypatch: Any,
) -> None:
    """An overproducing result must be rejected without unbounded iteration."""

    class OverproducingResult:
        def __init__(self) -> None:
            self.next_calls = 0

        def __len__(self) -> int:
            return 1

        def __iter__(self) -> "OverproducingResult":
            return self

        def __next__(self) -> tuple[float, ...]:
            self.next_calls += 1
            if self.next_calls > 2:
                raise RuntimeError("adapter iterated beyond declared length")
            return _valid_vector()

    output = OverproducingResult()
    provider = _provider(monkeypatch, _OutputModel(output))

    with pytest.raises(
        ValueError, match="Embedding backend returned malformed vectors"
    ):
        provider.embed_documents(("document",))

    assert output.next_calls == 2


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_embedding_output_control_flow_exception_identity_is_preserved(
    monkeypatch: Any, exception_type: type[BaseException]
) -> None:
    """Output materialization must re-raise the exact process-control object."""
    control_flow = exception_type("same-object")

    class RaisingResult:
        def __len__(self) -> int:
            raise control_flow

    provider = _provider(monkeypatch, _OutputModel(RaisingResult()))

    with pytest.raises(exception_type) as caught:
        provider.embed_documents(("document",))

    assert caught.value is control_flow


def test_transient_loader_failure_wakes_one_cohort_then_allows_retry(
    monkeypatch: Any,
) -> None:
    """One failed cohort must not poison the process or stampede into retries."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    secret = "secret-transient-loader-path"
    caller_gate = Barrier(17)
    load_started = Event()
    release_failure = Event()
    call_lock = Lock()
    loader_calls = 0

    def loader(identity: object) -> object:
        nonlocal loader_calls
        with call_lock:
            loader_calls += 1
            call_number = loader_calls
        if call_number == 1:
            load_started.set()
            assert release_failure.wait(timeout=2)
            raise OSError(secret)
        return SimpleNamespace(
            model=_OutputModel((_valid_vector(),)),
            tokenizer=object(),
            tokenizer_version="5.15.0",
        )

    monkeypatch.setattr(module, "_RUNTIME_REGISTRY", {})
    monkeypatch.setattr(module, "_load_runtime_backend", loader)
    provider = module.SentenceTransformerEmbeddingProvider(_settings())

    def invoke() -> BaseException | None:
        caller_gate.wait(timeout=2)
        try:
            provider.embed_documents(("document",))
        except BaseException as error:
            return error
        return None

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(invoke) for _ in range(16)]
        caller_gate.wait(timeout=2)
        assert load_started.wait(timeout=2)
        sleep(0.05)
        release_failure.set()
        failures = [future.result(timeout=2) for future in futures]

    assert loader_calls == 1
    assert all(type(error) is RuntimeError for error in failures)
    assert all(str(error) == "Embedding backend unavailable" for error in failures)
    assert all(secret not in str(error) for error in failures)
    assert all(error.__cause__ is None for error in failures if error is not None)
    assert all(error.__context__ is None for error in failures if error is not None)

    assert provider.embed_documents(("document",)) == (_valid_vector(),)
    assert loader_calls == 2


def test_each_new_call_gets_one_retry_after_loader_cohort_has_failed(
    monkeypatch: Any,
) -> None:
    """A retry may fail again, but it must be one bounded attempt per new call."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    loader_calls = 0

    def loader(identity: object) -> object:
        nonlocal loader_calls
        loader_calls += 1
        raise OSError("secret-repeat-loader-failure")

    monkeypatch.setattr(module, "_RUNTIME_REGISTRY", {})
    monkeypatch.setattr(module, "_load_runtime_backend", loader)
    provider = module.SentenceTransformerEmbeddingProvider(_settings())

    for _ in range(2):
        with pytest.raises(RuntimeError, match="Embedding backend unavailable"):
            provider.embed_documents(("document",))

    assert loader_calls == 2


@pytest.mark.parametrize(
    "exception_type", [KeyboardInterrupt, SystemExit, _LoaderAbort]
)
def test_loader_control_flow_exception_identity_is_preserved_and_retryable(
    monkeypatch: Any, exception_type: type[BaseException]
) -> None:
    """A loader control-flow signal resets state and remains the same object."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    control_flow = exception_type("same-loader-object")
    loader_calls = 0

    def loader(identity: object) -> object:
        nonlocal loader_calls
        loader_calls += 1
        if loader_calls == 1:
            raise control_flow
        return SimpleNamespace(
            model=_OutputModel((_valid_vector(),)),
            tokenizer=object(),
            tokenizer_version="5.15.0",
        )

    monkeypatch.setattr(module, "_RUNTIME_REGISTRY", {})
    monkeypatch.setattr(module, "_load_runtime_backend", loader)
    provider = module.SentenceTransformerEmbeddingProvider(_settings())

    with pytest.raises(exception_type) as caught:
        provider.embed_documents(("document",))

    assert caught.value is control_flow
    assert provider.embed_documents(("document",)) == (_valid_vector(),)
    assert loader_calls == 2
