"""Adversarial tests for SentenceTransformer embedding boundaries."""

from importlib import import_module
from math import inf, nan
from types import SimpleNamespace
from typing import Any

import pytest

from omf_retrieval.settings import Settings

DIMENSION = 1024


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
