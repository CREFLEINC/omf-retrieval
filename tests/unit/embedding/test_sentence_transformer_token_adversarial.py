"""Adversarial tests for source-backed tokenizer boundaries."""

from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest

from omf_retrieval.settings import Settings


class _IntSubclass(int):
    """Exercise exact token and offset integer validation."""


class _OutputTokenizer:
    is_fast = True

    def __init__(self, output: object) -> None:
        self.output = output

    def __call__(self, text: str, **kwargs: Any) -> object:
        return self.output


def _counter(monkeypatch: Any, tokenizer: object) -> object:
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    monkeypatch.setattr(module, "_RUNTIME_REGISTRY", {})
    monkeypatch.setattr(
        module,
        "_load_runtime_backend",
        lambda identity: SimpleNamespace(
            model=object(),
            tokenizer=tokenizer,
            tokenizer_version="5.15.0",
        ),
    )
    return module.SentenceTransformerTokenCounter(
        Settings(environment="test", embedding_device="cpu")
    )


@pytest.mark.parametrize(
    "output",
    [
        {},
        {"input_ids": [1], "offset_mapping": []},
        {"input_ids": [], "offset_mapping": [(0, 1)]},
        {"input_ids": [True], "offset_mapping": [(0, 1)]},
        {"input_ids": [-1], "offset_mapping": [(0, 1)]},
        {"input_ids": [_IntSubclass(1)], "offset_mapping": [(0, 1)]},
        {"input_ids": [1], "offset_mapping": [[0, 1]]},
        {"input_ids": [1], "offset_mapping": [(False, 1)]},
        {"input_ids": [1], "offset_mapping": [(_IntSubclass(0), 1)]},
        {"input_ids": [1], "offset_mapping": [(0, 0)]},
        {"input_ids": [1], "offset_mapping": [(1, 0)]},
        {"input_ids": [1], "offset_mapping": [(0, 4)]},
        {"input_ids": [1, 2], "offset_mapping": [(0, 2), (1, 3)]},
    ],
)
@pytest.mark.parametrize("method_name", ["encode", "offsets"])
def test_malformed_tokenizer_results_fail_closed(
    monkeypatch: Any, output: object, method_name: str
) -> None:
    """Malformed IDs or spans must not become chunk boundary coordinates."""
    counter = _counter(monkeypatch, _OutputTokenizer(output))

    with pytest.raises(
        ValueError, match="Tokenizer backend returned malformed data"
    ) as caught:
        getattr(counter, method_name)("abc")

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("method_name", ["encode", "offsets"])
def test_tokenizer_sequence_protocol_failures_are_sanitized(
    monkeypatch: Any, method_name: str
) -> None:
    """Hostile sequence operations must be bounded and source-free."""
    secret = "secret-from-token-sequence"

    class RaisingSequence:
        def __len__(self) -> int:
            return 1

        def __iter__(self) -> object:
            raise RuntimeError(secret)

    output = {
        "input_ids": RaisingSequence(),
        "offset_mapping": [(0, 1)],
    }
    counter = _counter(monkeypatch, _OutputTokenizer(output))

    with pytest.raises(
        ValueError, match="Tokenizer backend returned malformed data"
    ) as caught:
        getattr(counter, method_name)("a")

    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("method_name", ["encode", "offsets"])
def test_tokenizer_control_flow_exceptions_propagate(
    monkeypatch: Any,
    exception_type: type[BaseException],
    method_name: str,
) -> None:
    """Process control exceptions from tokenizer execution are never sanitized."""

    class RaisingTokenizer:
        is_fast = True

        def __call__(self, text: str, **kwargs: Any) -> object:
            raise exception_type("control-flow")

    counter = _counter(monkeypatch, RaisingTokenizer())

    with pytest.raises(exception_type, match="control-flow"):
        getattr(counter, method_name)("a")


@pytest.mark.parametrize("invalid_text", [7, True, None])
def test_token_counter_rejects_non_string_input_before_loading(
    monkeypatch: Any, invalid_text: object
) -> None:
    """Non-string input must not reach the tokenizer or lazy model loader."""
    module = import_module(
        "omf_retrieval.infrastructure.embedding.sentence_transformer"
    )
    loader_calls: list[object] = []
    monkeypatch.setattr(module, "_RUNTIME_REGISTRY", {})
    monkeypatch.setattr(
        module,
        "_load_runtime_backend",
        lambda identity: loader_calls.append(identity),
    )
    counter = module.SentenceTransformerTokenCounter(
        Settings(environment="test", embedding_device="cpu")
    )

    with pytest.raises(ValueError, match="Invalid tokenizer input"):
        counter.encode(invalid_text)

    assert loader_calls == []
