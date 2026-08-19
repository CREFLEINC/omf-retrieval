"""Unit tests for deterministic chunking contracts and identities."""

import re
import traceback
from collections.abc import Iterator, Sequence
from dataclasses import FrozenInstanceError, replace
from importlib import import_module
from typing import Any

import pytest

from omf_retrieval.application.indexing.ports import (
    ChunkConfig,
    ParsedSection,
    TokenizerDescriptor,
)
from omf_retrieval.infrastructure.source.chunker import (
    CHUNKER_VERSION,
    chunk_config_identity_hash,
)
from omf_retrieval.infrastructure.source.markdown import MarkdownItParser


class _IntSubclass(int):
    """Exercise rejection of integer subclasses at contract boundaries."""


class _StrSubclass(str):
    """Exercise rejection of string subclasses at contract boundaries."""


class _TupleSubclass(tuple):
    """Exercise rejection of tuple subclasses at contract boundaries."""


class _ChunkConfigSubclass(ChunkConfig):
    """Represent an invalid config subtype at an exact DTO boundary."""


class _TokenizerDescriptorSubclass(TokenizerDescriptor):
    """Represent an invalid descriptor subtype at an exact DTO boundary."""


class _ParsedSectionSubclass(ParsedSection):
    """Represent an invalid section subtype at an exact DTO boundary."""


class _FakeTokenCounter:
    """Provide deterministic source-backed tokens for the protocol contract."""

    def encode(self, text: str) -> tuple[int, ...]:
        """Map each source character to one deterministic token."""
        return tuple(ord(character) for character in text)

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        """Map every fake token to its exact one-character source slice."""
        return tuple((index, index + 1) for index in range(len(text)))


class _PairTokenCounter:
    """Tokenize pairs so tests distinguish token offsets from character offsets."""

    def encode(self, text: str) -> tuple[int, ...]:
        """Return one stable token for each consecutive character pair."""
        return tuple(
            ord(text[index]) for index in range(0, len(text), 2) if text[index:]
        )

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        """Return exact half-open spans for each pair token."""
        return tuple(
            (index, min(index + 2, len(text))) for index in range(0, len(text), 2)
        )


class _CountingTokenCounter(_FakeTokenCounter):
    """Count adapter input work and optionally stop superlinear regressions."""

    def __init__(self, *, maximum_work: int | None = None) -> None:
        self.maximum_work = maximum_work
        self.encode_inputs: list[str] = []
        self.offset_inputs: list[str] = []
        self.input_work = 0

    @property
    def calls(self) -> int:
        return len(self.encode_inputs) + len(self.offset_inputs)

    def _record(self, inputs: list[str], text: str) -> None:
        inputs.append(text)
        self.input_work += len(text)
        if self.maximum_work is not None and self.input_work > self.maximum_work:
            raise RuntimeError("linear token work budget exceeded")

    def encode(self, text: str) -> tuple[int, ...]:
        self._record(self.encode_inputs, text)
        return super().encode(text)

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        self._record(self.offset_inputs, text)
        return super().offsets(text)


class _BoundarySensitiveTokenCounter:
    """Retokenize a heading boundary while keeping every offset source-backed."""

    def __init__(
        self, heading_prefix: str, *, raw_widths: tuple[int, ...] = (1,)
    ) -> None:
        self._heading_prefix = heading_prefix
        self._raw_widths = raw_widths

    def _spans(self, text: str) -> tuple[tuple[int, int], ...]:
        if text == self._heading_prefix:
            return ((0, len(text)),)
        if text.startswith(self._heading_prefix):
            return tuple((index, index + 1) for index in range(len(text)))

        spans: list[tuple[int, int]] = []
        cursor = 0
        width_index = 0
        while cursor < len(text):
            end = min(cursor + self._raw_widths[width_index], len(text))
            spans.append((cursor, end))
            cursor = end
            width_index = (width_index + 1) % len(self._raw_widths)
        return tuple(spans)

    def encode(self, text: str) -> tuple[int, ...]:
        return tuple(ord(text[start]) for start, _ in self._spans(text))

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        return self._spans(text)


class _EncodeResultCounter(_FakeTokenCounter):
    def __init__(self, result: object) -> None:
        self._result = result

    def encode(self, text: str) -> object:
        return self._result


class _OffsetResultCounter(_FakeTokenCounter):
    def __init__(self, result: object) -> None:
        self._result = result

    def offsets(self, text: str) -> object:
        return self._result


class _RaisingTokenCounter(_FakeTokenCounter):
    def encode(self, text: str) -> tuple[int, ...]:
        raise RuntimeError("secret-tokenizer-detail")


class _SecretTokenizerException(Exception):
    """Represent an adapter-specific ordinary exception outside any allowlist."""


class _SecretAdapterError(Exception):
    """Represent an ordinary failure raised while consuming adapter output."""


class _EncodeFailureCounter(_FakeTokenCounter):
    def __init__(self, error_type: type[BaseException]) -> None:
        self._error_type = error_type

    def encode(self, text: str) -> tuple[int, ...]:
        raise self._error_type("secret-encode-detail")


class _OffsetFailureCounter(_FakeTokenCounter):
    def __init__(self, error_type: type[BaseException]) -> None:
        self._error_type = error_type

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        raise self._error_type("secret-offset-detail")


class _EncodePropertyFailureCounter:
    def __init__(self, error_type: type[BaseException]) -> None:
        self._error_type = error_type

    @property
    def encode(self) -> object:
        raise self._error_type("secret-encode-property-detail")

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        return ()


class _OffsetPropertyFailureCounter:
    def __init__(self, error_type: type[BaseException]) -> None:
        self._error_type = error_type

    def encode(self, text: str) -> tuple[int, ...]:
        return ()

    @property
    def offsets(self) -> object:
        raise self._error_type("secret-offset-property-detail")


class _FailingIterator(Iterator[object]):
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def __next__(self) -> object:
        raise self._error


class _FailingSequence(Sequence[object]):
    def __init__(
        self,
        items: tuple[object, ...],
        operation: str,
        error: BaseException,
    ) -> None:
        self._items = items
        self._operation = operation
        self._error = error

    def __len__(self) -> int:
        if self._operation == "len":
            raise self._error
        return len(self._items)

    def __iter__(self) -> Iterator[object]:
        if self._operation == "iter":
            raise self._error
        if self._operation == "next":
            return _FailingIterator(self._error)
        return super().__iter__()

    def __getitem__(self, index: int | slice) -> object:
        if self._operation == "getitem":
            raise self._error
        return self._items[index]


class _SequenceOperationFailureCounter(_FakeTokenCounter):
    def __init__(
        self,
        side: str,
        operation: str,
        error: BaseException,
    ) -> None:
        self._side = side
        self._operation = operation
        self._error = error

    def encode(self, text: str) -> object:
        tokens = super().encode(text)
        if self._side == "encode":
            return _FailingSequence(tokens, self._operation, self._error)
        return tokens

    def offsets(self, text: str) -> object:
        offsets = super().offsets(text)
        if self._side == "offsets":
            return _FailingSequence(offsets, self._operation, self._error)
        return offsets


class _ExplicitMismatchSequence(Sequence[object]):
    def __init__(
        self,
        *,
        declared_length: int,
        indexed_items: tuple[object, ...],
        iterated_items: tuple[object, ...],
    ) -> None:
        self._declared_length = declared_length
        self._indexed_items = indexed_items
        self._iterated_items = iterated_items

    def __len__(self) -> int:
        return self._declared_length

    def __iter__(self) -> Iterator[object]:
        return iter(self._iterated_items)

    def __getitem__(self, index: int | slice) -> object:
        return self._indexed_items[index]


class _FallbackMismatchSequence(Sequence[object]):
    def __init__(
        self, *, declared_length: int, indexed_items: tuple[object, ...]
    ) -> None:
        self._declared_length = declared_length
        self._indexed_items = indexed_items

    def __len__(self) -> int:
        return self._declared_length

    def __getitem__(self, index: int | slice) -> object:
        return self._indexed_items[index]


class _GuardedInfiniteIterator(Iterator[object]):
    def __init__(self, item: object, *, maximum_calls: int) -> None:
        self._item = item
        self._maximum_calls = maximum_calls
        self.calls = 0

    def __next__(self) -> object:
        self.calls += 1
        if self.calls > self._maximum_calls:
            raise _SecretAdapterError("secret-unbounded-materialization")
        return self._item


class _GuardedInfiniteSequence(Sequence[object]):
    def __init__(self, item: object, *, declared_length: int) -> None:
        self._item = item
        self._declared_length = declared_length
        self.iterator = _GuardedInfiniteIterator(
            item, maximum_calls=declared_length + 1
        )

    def __len__(self) -> int:
        return self._declared_length

    def __iter__(self) -> Iterator[object]:
        return self.iterator

    def __getitem__(self, index: int | slice) -> object:
        return self._item


class _HugeDeclaredIterator(Iterator[object]):
    def __init__(self, item: object) -> None:
        self._item = item
        self.next_calls = 0

    def __next__(self) -> object:
        self.next_calls += 1
        if self.next_calls > 2:
            raise _SecretAdapterError("secret-huge-declared-length-consumption")
        return self._item


class _HugeDeclaredSequence(Sequence[object]):
    def __init__(self, item: object) -> None:
        self._item = item
        self.iterator = _HugeDeclaredIterator(item)
        self.iter_calls = 0
        self.getitem_calls = 0

    def __len__(self) -> int:
        return 10**9

    def __iter__(self) -> Iterator[object]:
        self.iter_calls += 1
        return self.iterator

    def __getitem__(self, index: int | slice) -> object:
        self.getitem_calls += 1
        return self._item


class _HugeDeclaredLengthCounter(_FakeTokenCounter):
    def __init__(self, side: str) -> None:
        self._side = side
        self.result: _HugeDeclaredSequence | None = None

    def _huge_result(self, item: object) -> _HugeDeclaredSequence:
        result = _HugeDeclaredSequence(item)
        self.result = result
        return result

    def encode(self, text: str) -> object:
        if self._side == "encode":
            return self._huge_result(1)
        return super().encode(text)

    def offsets(self, text: str) -> object:
        if self._side == "offsets":
            return self._huge_result((0, 1))
        return super().offsets(text)


class _DeclaredLengthSequence(Sequence[object]):
    def __init__(self, items: tuple[object, ...], *, declared_length: int) -> None:
        self._items = items
        self._declared_length = declared_length
        self.iter_calls = 0
        self.getitem_calls = 0

    def __len__(self) -> int:
        return self._declared_length

    def __iter__(self) -> Iterator[object]:
        self.iter_calls += 1
        return iter(self._items)

    def __getitem__(self, index: int | slice) -> object:
        self.getitem_calls += 1
        return self._items[index]


class _DeclaredLengthCounter(_FakeTokenCounter):
    def __init__(self, side: str, *, extra_declared_items: int) -> None:
        self._side = side
        self._extra_declared_items = extra_declared_items
        self.results: list[_DeclaredLengthSequence] = []

    def _result(self, items: tuple[object, ...]) -> _DeclaredLengthSequence:
        result = _DeclaredLengthSequence(
            items,
            declared_length=len(items) + self._extra_declared_items,
        )
        self.results.append(result)
        return result

    def encode(self, text: str) -> object:
        tokens = super().encode(text)
        return self._result(tokens) if self._side == "encode" else tokens

    def offsets(self, text: str) -> object:
        offsets = super().offsets(text)
        return self._result(offsets) if self._side == "offsets" else offsets


class _SequenceLengthMismatchCounter(_FakeTokenCounter):
    def __init__(self, side: str, mismatch: str) -> None:
        self._side = side
        self._mismatch = mismatch
        self.infinite_sequence: _GuardedInfiniteSequence | None = None

    def _mismatched(self, items: tuple[object, ...]) -> Sequence[object]:
        declared_length = len(items)
        if self._mismatch == "len-over":
            return _ExplicitMismatchSequence(
                declared_length=declared_length + 1,
                indexed_items=items,
                iterated_items=items,
            )
        if self._mismatch == "len-under":
            return _ExplicitMismatchSequence(
                declared_length=declared_length - 1,
                indexed_items=items,
                iterated_items=items,
            )
        if self._mismatch == "iterator-short":
            return _ExplicitMismatchSequence(
                declared_length=declared_length,
                indexed_items=items,
                iterated_items=items[:-1],
            )
        if self._mismatch == "iterator-long":
            return _ExplicitMismatchSequence(
                declared_length=declared_length,
                indexed_items=items,
                iterated_items=(*items, items[-1]),
            )
        if self._mismatch == "fallback-short":
            return _FallbackMismatchSequence(
                declared_length=declared_length,
                indexed_items=items[:-1],
            )
        if self._mismatch == "fallback-long":
            return _FallbackMismatchSequence(
                declared_length=declared_length,
                indexed_items=(*items, items[-1]),
            )
        if self._mismatch == "exact-iterator":
            return _ExplicitMismatchSequence(
                declared_length=declared_length,
                indexed_items=items,
                iterated_items=items,
            )
        if self._mismatch == "exact-fallback":
            return _FallbackMismatchSequence(
                declared_length=declared_length,
                indexed_items=items,
            )
        sequence = _GuardedInfiniteSequence(items[-1], declared_length=declared_length)
        self.infinite_sequence = sequence
        return sequence

    def encode(self, text: str) -> object:
        tokens = super().encode(text)
        if self._side == "encode":
            return self._mismatched(tokens)
        return tokens

    def offsets(self, text: str) -> object:
        offsets = super().offsets(text)
        if self._side == "offsets":
            return self._mismatched(offsets)
        return offsets


def _descriptor() -> TokenizerDescriptor:
    return TokenizerDescriptor(
        model_name="model",
        revision="revision",
        library_name="library",
        library_version="1.0",
        add_special_tokens=False,
    )


def _chunker(
    counter: object | None = None,
    *,
    config: ChunkConfig | None = None,
) -> Any:
    return _chunker_type()(
        counter if counter is not None else _FakeTokenCounter(),
        _descriptor(),
        config if config is not None else ChunkConfig(),
    )


def _chunker_type() -> Any:
    chunker_type = getattr(
        import_module("omf_retrieval.infrastructure.source.chunker"),
        "ParentChildChunker",
        None,
    )
    assert chunker_type is not None, "ParentChildChunker must be public"
    return chunker_type


def _section(source: str, *, ordinal: int = -1) -> ParsedSection:
    parsed = MarkdownItParser().parse(source)
    return parsed.sections[ordinal]


def test_parent_child_chunker_exposes_the_approved_public_split_api() -> None:
    """Removing the public splitter makes approved parser output unusable."""
    chunks = _chunker().split(_section("# A\nbody\n"), parser_version="parser-v1")

    assert len(chunks) == 1


@pytest.mark.parametrize("source", ["# Empty\n", "# Blank\n \t\r\n\n"])
def test_empty_or_blank_section_produces_no_children(source: str) -> None:
    """Heading-only and blank bodies must not create unsearchable children."""
    chunks = _chunker().split(_section(source), parser_version="parser-v1")

    assert chunks == ()


def test_search_text_at_exact_soft_max_remains_one_child_with_heading_path() -> None:
    """Splitting at the 600-token inclusive boundary would over-fragment text."""
    body = "x" * 596
    section = _section(f"# A\n## B\n{body}")

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert len(chunks) == 1
    assert chunks[0].raw_text == body
    assert chunks[0].search_text == f"A\nB\n{body}"
    assert chunks[0].token_count == 600
    assert (chunks[0].line_start, chunks[0].line_end) == (3, 3)


def test_long_normal_blocks_pack_to_target_without_exceeding_soft_max() -> None:
    """Ignoring block-aware target packing would fragment or oversize children."""
    first_paragraph = "a" * 350 + "\n\n"
    second_paragraph = "b" * 100 + "\n\n"
    third_paragraph = "c" * 300 + "\n"
    section = _section(f"# H\n{first_paragraph}{second_paragraph}{third_paragraph}")
    counter = _FakeTokenCounter()

    chunks = _chunker(counter).split(section, parser_version="parser-v1")

    assert len(chunks) == 2
    assert chunks[0].raw_text == first_paragraph + second_paragraph[:-1]
    assert chunks[0].token_count == 455
    assert chunks[1].raw_text == (chunks[0].raw_text[-64:] + "\n" + third_paragraph)
    assert chunks[1].raw_text.startswith(chunks[0].raw_text[-64:])
    assert (chunks[0].line_start, chunks[0].line_end) == (2, 4)
    assert (chunks[1].line_start, chunks[1].line_end) == (4, 6)
    assert all(
        chunk.token_count == len(counter.encode(chunk.search_text)) <= 600
        for chunk in chunks
    )


def test_oversized_normal_block_splits_at_token_offsets_with_exact_overlap() -> None:
    """Character slicing would break target size and the exact token overlap."""
    body = "ab" * 601
    section = _section(body)
    counter = _PairTokenCounter()

    chunks = _chunker(counter).split(section, parser_version="parser-v1")

    assert [chunk.token_count for chunk in chunks] == [400, 265]
    assert chunks[0].raw_text == body[:800]
    assert chunks[1].raw_text == body[672:]
    assert chunks[1].raw_text[:128] == chunks[0].raw_text[-128:]
    assert chunks[0].raw_text + chunks[1].raw_text[128:] == body
    assert all(chunk.token_count <= 600 for chunk in chunks)


def _reconstruct_character_chunks(chunks: Sequence[object]) -> str:
    first, *remaining = chunks
    reconstructed = first.raw_text
    previous = first
    for chunk in remaining:
        overlap = min(64, len(previous.raw_text) - 1)
        if overlap:
            assert chunk.raw_text.startswith(previous.raw_text[-overlap:])
        reconstructed += chunk.raw_text[overlap:]
        previous = chunk
    return reconstructed


def _reconstruct_variable_overlap(chunks: Sequence[object]) -> str:
    first, *remaining = chunks
    reconstructed = first.raw_text
    previous = first
    for chunk in remaining:
        overlap = next(
            (
                length
                for length in range(
                    min(len(previous.raw_text), len(chunk.raw_text)), 0, -1
                )
                if previous.raw_text[-length:] == chunk.raw_text[:length]
            ),
            0,
        )
        assert overlap < len(chunk.raw_text)
        reconstructed += chunk.raw_text[overlap:]
        previous = chunk
    return reconstructed


@pytest.mark.parametrize("prefix_tokens", [399, 400, 450, 599])
def test_long_legal_heading_uses_available_source_budget_and_terminates(
    prefix_tokens: int,
) -> None:
    """A legal heading below soft max must leave deterministic source children."""
    body_length = 602 - prefix_tokens
    body = "".join(chr(0x4E00 + index) for index in range(body_length))
    heading = "H" * (prefix_tokens - 1)
    source = f"# {heading}\n{body}"
    counter = _CountingTokenCounter(maximum_work=1_000_000)

    chunks = _chunker(counter).split(_section(source), parser_version="parser-v1")
    repeated = _chunker(_FakeTokenCounter()).split(
        _section(source), parser_version="parser-v1"
    )

    assert chunks
    assert chunks == repeated
    assert all(chunk.raw_text and chunk.token_count <= 600 for chunk in chunks)
    assert _reconstruct_character_chunks(chunks) == body


@pytest.mark.parametrize(
    (
        "prefix_tokens",
        "expected_first_source",
        "expected_overlap",
        "expected_chunks",
    ),
    [(599, 1, 0, 3), (598, 2, 1, 2)],
)
def test_tiny_heading_source_budget_reduces_overlap_to_preserve_progress(
    prefix_tokens: int,
    expected_first_source: int,
    expected_overlap: int,
    expected_chunks: int,
) -> None:
    """Overlap may never consume every source token in a forced window."""
    body = "가나다"
    heading = "H" * (prefix_tokens - 1)
    counter = _CountingTokenCounter(maximum_work=100_000)

    chunks = _chunker(counter).split(
        _section(f"# {heading}\n{body}"), parser_version="parser-v1"
    )

    assert len(chunks) == expected_chunks
    assert len(chunks[0].raw_text) == expected_first_source
    if expected_overlap:
        assert chunks[1].raw_text.startswith(chunks[0].raw_text[-expected_overlap:])
    else:
        assert not chunks[1].raw_text.startswith(chunks[0].raw_text)
    assert _reconstruct_character_chunks(chunks) == body


def test_heading_at_soft_max_rejects_nonempty_overflow_without_source_leak() -> None:
    """A heading consuming the soft limit cannot form a nonempty child."""
    heading = "H" * 599
    secret_body = "OMF-HEADING-SECRET"

    with pytest.raises(ValueError) as caught:
        _chunker().split(
            _section(f"# {heading}\n{secret_body}"), parser_version="parser-v1"
        )

    assert str(caught.value) == "Heading path leaves no room for child source text"
    assert caught.value.__cause__ is None
    assert secret_body not in str(caught.value)


_HEADING_TARGET_TOKENS = 400
_HEADING_SOFT_MAX_TOKENS = 600
_HEADING_OVERLAP_TOKENS = 64
_HEADING_MIN_TARGET_SOURCE_TOKENS = 128
_HEADING_WORK_MULTIPLIER = 24
_HEADING_EMITTED_TOKEN_MULTIPLIER = 10


def _assert_heading_window_invariants(
    counter: _CountingTokenCounter,
    chunks: Sequence[object],
    body: str,
    *,
    prefix_tokens: int,
    repeated: Sequence[object],
    split_work: int,
    split_calls: int,
) -> None:
    actual_counts = [len(counter.encode(chunk.search_text)) for chunk in chunks]
    assert actual_counts == [chunk.token_count for chunk in chunks]
    assert all(count <= _HEADING_SOFT_MAX_TOKENS for count in actual_counts)
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert all((chunk.line_start, chunk.line_end) == (2, 2) for chunk in chunks)
    assert chunks == repeated
    assert [chunk.chunk_hash for chunk in chunks] == [
        chunk.chunk_hash for chunk in repeated
    ]
    assert all(re.fullmatch(r"[0-9a-f]{64}", chunk.chunk_hash) for chunk in chunks)

    starts = [body.find(chunk.raw_text) for chunk in chunks]
    assert starts[0] == 0
    assert all(start >= 0 for start in starts)
    reconstructed = chunks[0].raw_text
    for index, (previous, chunk) in enumerate(
        zip(chunks, chunks[1:], strict=False), start=1
    ):
        previous_start = starts[index - 1]
        chunk_start = starts[index]
        previous_end = previous_start + len(previous.raw_text)
        overlap = previous_end - chunk_start
        assert chunk_start - previous_start >= (len(previous.raw_text) + 1) // 2
        assert overlap == min(_HEADING_OVERLAP_TOKENS, len(previous.raw_text) // 2)
        if len(previous.raw_text) >= 2 * _HEADING_OVERLAP_TOKENS:
            assert overlap == _HEADING_OVERLAP_TOKENS
        reconstructed += chunk.raw_text[overlap:]
    assert reconstructed == body

    target_budget = _HEADING_TARGET_TOKENS - prefix_tokens
    soft_budget = _HEADING_SOFT_MAX_TOKENS - prefix_tokens
    use_target_window = target_budget >= _HEADING_MIN_TARGET_SOURCE_TOKENS
    selected_budget = target_budget if use_target_window else soft_budget
    selected_overlap = min(_HEADING_OVERLAP_TOKENS, selected_budget // 2)
    selected_advance = selected_budget - selected_overlap
    selected_forced_tokens = len(body) - soft_budget
    selected_bound = (
        selected_forced_tokens + selected_advance - 1
    ) // selected_advance + 1
    assert len(chunks[0].raw_text) == selected_budget
    assert len(chunks) <= selected_bound

    if not use_target_window:
        soft_overlap = min(_HEADING_OVERLAP_TOKENS, soft_budget // 2)
        soft_advance = soft_budget - soft_overlap
        theoretical_soft_children = (
            selected_forced_tokens + soft_advance - 1
        ) // soft_advance + 1
        assert len(chunks) <= theoretical_soft_children

    original_search_tokens = prefix_tokens + len(body)
    assert split_work <= _HEADING_WORK_MULTIPLIER * original_search_tokens
    assert split_calls <= 2 * len(chunks) + 4
    assert sum(actual_counts) <= (
        _HEADING_EMITTED_TOKEN_MULTIPLIER * original_search_tokens
    )


def _split_character_heading(
    prefix_tokens: int, body_length: int
) -> tuple[_CountingTokenCounter, str, tuple[object, ...]]:
    heading = "H" * (prefix_tokens - 1)
    body = "".join(chr(0x6000 + index) for index in range(body_length))
    source = f"# {heading}\n{body}"
    counter = _CountingTokenCounter()

    chunks = _chunker(counter).split(_section(source), parser_version="parser-v1")
    split_work = counter.input_work
    split_calls = counter.calls
    repeated = _chunker().split(_section(source), parser_version="parser-v1")

    assert len(_FakeTokenCounter().encode(heading + "\n")) == prefix_tokens
    _assert_heading_window_invariants(
        counter,
        chunks,
        body,
        prefix_tokens=prefix_tokens,
        repeated=repeated,
        split_work=split_work,
        split_calls=split_calls,
    )
    return counter, body, chunks


@pytest.mark.parametrize(
    "prefix_tokens",
    [
        271,
        272,
        273,
        333,
        334,
        335,
        336,
        337,
        399,
        400,
        401,
    ],
)
@pytest.mark.parametrize("body_length", [1_000, 4_000])
def test_heading_prefix_sweep_bounds_window_progress_and_amplification(
    prefix_tokens: int, body_length: int
) -> None:
    """Every chosen window bounds children, work, tokens, and cursor progress."""
    _split_character_heading(prefix_tokens, body_length)


@pytest.mark.parametrize(
    ("prefix_tokens", "expected_first_source", "expected_overlap"),
    [(472, 128, 64), (473, 127, 63)],
)
@pytest.mark.parametrize("body_length", [1_000, 4_000])
def test_soft_window_half_progress_boundary_catches_threshold_mutations(
    prefix_tokens: int,
    expected_first_source: int,
    expected_overlap: int,
    body_length: int,
) -> None:
    """The 128/127 soft-window boundary preserves at least half new source."""
    _, body, chunks = _split_character_heading(prefix_tokens, body_length)

    first_start = body.find(chunks[0].raw_text)
    second_start = body.find(chunks[1].raw_text)
    actual_overlap = first_start + len(chunks[0].raw_text) - second_start
    assert len(chunks[0].raw_text) == expected_first_source
    assert actual_overlap == expected_overlap


def _boundary_sensitive_split(
    prefix_tokens: int,
    body: str,
    *,
    raw_widths: tuple[int, ...] = (1,),
) -> tuple[_BoundarySensitiveTokenCounter, tuple[object, ...]]:
    heading = "H" * (prefix_tokens - 1)
    heading_prefix = heading + "\n"
    counter = _BoundarySensitiveTokenCounter(heading_prefix, raw_widths=raw_widths)
    chunks = _chunker(counter).split(
        _section(f"# {heading}\n{body}"), parser_version="parser-v1"
    )
    return counter, chunks


def _assert_boundary_sensitive_chunks(
    counter: _BoundarySensitiveTokenCounter,
    chunks: Sequence[object],
    body: str,
) -> None:
    actual_counts = [len(counter.encode(chunk.search_text)) for chunk in chunks]
    assert actual_counts == [chunk.token_count for chunk in chunks]
    assert all(count <= 600 for count in actual_counts), (
        f"raw lengths={[len(chunk.raw_text) for chunk in chunks]}, "
        f"actual counts={actual_counts}"
    )
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert _reconstruct_variable_overlap(chunks) == body


def test_boundary_sensitive_final_pending_is_rechecked_until_within_soft_max() -> None:
    """One corrective window cannot justify returning an unchecked remainder."""
    body = "".join(chr(0x4E00 + index) for index in range(9))

    counter, chunks = _boundary_sensitive_split(599, body)
    repeated_counter, repeated = _boundary_sensitive_split(599, body)

    _assert_boundary_sensitive_chunks(counter, chunks, body)
    _assert_boundary_sensitive_chunks(repeated_counter, repeated, body)
    assert chunks == repeated


@pytest.mark.parametrize(
    ("prefix_tokens", "body_length"),
    [(399, 410), (400, 405), (450, 310), (599, 9)],
)
def test_boundary_sensitive_heading_may_require_multiple_corrective_windows(
    prefix_tokens: int, body_length: int
) -> None:
    """Every bounded remainder is rechecked after each overlap adjustment."""
    body = "".join(chr(0x5000 + index) for index in range(body_length))

    counter, chunks = _boundary_sensitive_split(prefix_tokens, body)

    _assert_boundary_sensitive_chunks(counter, chunks, body)
    assert len(chunks) >= 2


@pytest.mark.parametrize(
    ("prefix_tokens", "raw_widths", "body_length"),
    [
        (599, (1,), 9),
        (598, (2,), 12),
        (597, (1, 3, 2), 18),
    ],
    ids=["character", "pair", "nonuniform"],
)
def test_boundary_sensitive_correction_preserves_raw_token_boundaries(
    prefix_tokens: int,
    raw_widths: tuple[int, ...],
    body_length: int,
) -> None:
    """Character, pair, and nonuniform source offsets all make progress."""
    body = "".join(chr(0x5200 + index) for index in range(body_length))

    counter, chunks = _boundary_sensitive_split(
        prefix_tokens, body, raw_widths=raw_widths
    )

    _assert_boundary_sensitive_chunks(counter, chunks, body)


def test_boundary_sensitive_atomic_unit_remains_bounded_without_overlap() -> None:
    """The already-complete atomic cursor loop keeps every forced piece bounded."""
    heading = "H" * 798
    heading_prefix = heading + "\n"
    body = "- " + "".join(chr(0x5400 + index) for index in range(7))
    counter = _BoundarySensitiveTokenCounter(heading_prefix)

    chunks = _chunker(counter).split(
        _section(f"# {heading}\n{body}"), parser_version="parser-v1"
    )

    assert "".join(chunk.raw_text for chunk in chunks) == body
    assert all(
        chunk.token_count == len(counter.encode(chunk.search_text)) <= 800
        for chunk in chunks
    )
    assert all(chunk.warnings for chunk in chunks)


_LINEAR_TOKEN_WORK_MULTIPLIER = 12


def _long_split_source(kind: str, payload_length: int) -> str:
    payload = "x" * payload_length
    if kind == "normal":
        return payload + "\n"
    if kind == "table-row":
        return f"| {payload} |\n|---|\n"
    if kind == "list-item":
        return f"- {payload}\n"
    return f"> {payload}\n"


def _assert_long_split_contract(
    kind: str, source_body: str, chunks: Sequence[object]
) -> None:
    limit = 600 if kind == "normal" else 800
    assert chunks
    assert all(chunk.raw_text and chunk.token_count <= limit for chunk in chunks)
    if kind == "normal":
        assert _reconstruct_character_chunks(chunks) == source_body
        assert all(
            chunk.raw_text.startswith(previous.raw_text[-64:])
            for previous, chunk in zip(chunks, chunks[1:], strict=False)
        )
        return
    assert "".join(chunk.raw_text for chunk in chunks) == source_body
    assert all(len(chunk.warnings) == 1 for chunk in chunks)


def _measure_long_split(
    kind: str, payload_length: int, *, guarded: bool = False
) -> tuple[_CountingTokenCounter, str, tuple[object, ...]]:
    source_body = _long_split_source(kind, payload_length)
    maximum_work = _LINEAR_TOKEN_WORK_MULTIPLIER * len(source_body) if guarded else None
    counter = _CountingTokenCounter(maximum_work=maximum_work)
    chunks = _chunker(counter).split(
        _section(f"# H\n{source_body}"), parser_version="parser-v1"
    )
    _assert_long_split_contract(kind, source_body, chunks)
    return counter, source_body, chunks


@pytest.mark.parametrize("kind", ["normal", "table-row", "list-item", "blockquote"])
def test_long_single_unit_token_work_scales_linearly(kind: str) -> None:
    """Doubling one source unit must not repeatedly tokenize its full suffix."""
    small, small_body, _ = _measure_long_split(kind, 4_000)
    large, large_body, _ = _measure_long_split(kind, 8_000)

    assert large.input_work / small.input_work < 2.5
    assert small.input_work <= _LINEAR_TOKEN_WORK_MULTIPLIER * len(small_body)
    assert large.input_work <= _LINEAR_TOKEN_WORK_MULTIPLIER * len(large_body)
    assert sum(len(text) > 800 for text in large.offset_inputs) == 1
    assert sum(len(text) > 800 for text in large.encode_inputs) <= 4
    assert large.calls <= 20 + len(large_body) // 100


@pytest.mark.parametrize("kind", ["normal", "table-row", "list-item", "blockquote"])
def test_long_single_unit_has_a_finite_linear_work_guard(kind: str) -> None:
    """A 16k unit must stay within the documented 12x adapter-input budget."""
    counter, source_body, _ = _measure_long_split(kind, 16_000, guarded=True)

    assert counter.input_work <= _LINEAR_TOKEN_WORK_MULTIPLIER * len(source_body)


@pytest.mark.parametrize("kind", ["normal", "table-row", "list-item", "blockquote"])
def test_very_long_single_unit_keeps_near_doubling_token_work(kind: str) -> None:
    """The corrective remainder check must retain linear 32k-to-64k scaling."""
    small, small_body, _ = _measure_long_split(kind, 32_000, guarded=True)
    large, large_body, _ = _measure_long_split(kind, 64_000, guarded=True)

    assert large.input_work / small.input_work < 2.2
    assert small.input_work <= _LINEAR_TOKEN_WORK_MULTIPLIER * len(small_body)
    assert large.input_work <= _LINEAR_TOKEN_WORK_MULTIPLIER * len(large_body)


@pytest.mark.parametrize(
    ("atomic_text", "expected_atomic", "expected_after"),
    [
        ("| h |\n|---|\n| v |\n", "| h |\n|---|\n| v |\n", "\n" + "z" * 300 + "\n"),
        ("- first\n- second\n", "- first\n- second\n\n", "z" * 300 + "\n"),
        ("> first\n>\n> second\n", "> first\n>\n> second\n", "\n" + "z" * 300 + "\n"),
    ],
    ids=["table", "list", "quote"],
)
def test_preserved_atomic_block_stays_separate_from_normal_text(
    atomic_text: str, expected_atomic: str, expected_after: str
) -> None:
    """Merging a small atomic block with normal text would lose its boundary."""
    before = "n" * 300 + "\n\n"
    after = "\n" + "z" * 300 + "\n"
    section = _section(f"# H\n{before}{atomic_text}{after}")

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert [chunk.raw_text for chunk in chunks] == [
        before,
        expected_atomic,
        expected_after,
    ]
    assert "".join(chunk.raw_text for chunk in chunks) == section.body
    assert chunks[1].warnings == ()
    assert chunks[1].token_count <= 800


@pytest.mark.parametrize(
    ("atomic_text", "expected_chunks"),
    [
        (
            "| " + "h" * 300 + " |\n|---|\n"
            "| " + "a" * 300 + " |\n"
            "| " + "b" * 300 + " |\n",
            (
                "| " + "h" * 300 + " |\n|---|\n| " + "a" * 300 + " |\n",
                "| " + "b" * 300 + " |\n",
            ),
        ),
        (
            "- " + "a" * 300 + "\n- " + "b" * 300 + "\n- " + "c" * 300 + "\n",
            (
                "- " + "a" * 300 + "\n- " + "b" * 300 + "\n",
                "- " + "c" * 300 + "\n",
            ),
        ),
        (
            "> " + "a" * 300 + "\n>\n> " + "b" * 300 + "\n>\n> " + "c" * 300 + "\n",
            (
                "> " + "a" * 300 + "\n>\n> " + "b" * 300 + "\n>\n",
                "> " + "c" * 300 + "\n",
            ),
        ),
    ],
    ids=["table-rows", "direct-list-items", "quote-children"],
)
def test_oversized_atomic_blocks_split_only_at_unit_boundaries(
    atomic_text: str, expected_chunks: tuple[str, ...]
) -> None:
    """Splitting preserved structures inside a fitting unit corrupts evidence."""
    section = _section(f"# H\n{atomic_text}")

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert tuple(chunk.raw_text for chunk in chunks) == expected_chunks
    assert "".join(chunk.raw_text for chunk in chunks) == atomic_text
    assert all(chunk.token_count <= 800 and not chunk.warnings for chunk in chunks)


@pytest.mark.parametrize(
    ("atomic_text", "warning_kind", "warning_line"),
    [
        ("- " + "x" * 900 + "\n", "list_item", 2),
        ("> " + "x" * 900 + "\n", "paragraph", 2),
        ("| h |\n|---|\n| " + "x" * 900 + " |\n", "table_row", 4),
    ],
    ids=["list-item", "quote-child", "table-row"],
)
def test_single_oversized_atomic_unit_uses_offsets_and_safe_warning_only(
    atomic_text: str,
    warning_kind: str,
    warning_line: int,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Silent token slicing or source-bearing logs would hide atomic damage."""
    section = _section(f"# H\n{atomic_text}")

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert "".join(chunk.raw_text for chunk in chunks) == atomic_text
    warning_chunks = tuple(chunk for chunk in chunks if chunk.warnings)
    assert len(warning_chunks) >= 2
    assert all(
        [
            (
                warning.code,
                warning.block_kind,
                warning.line_start,
                warning.line_end,
            )
            for warning in chunk.warnings
        ]
        == [
            (
                "oversized_atomic_unit_token_split",
                warning_kind,
                warning_line,
                warning_line,
            )
        ]
        for chunk in warning_chunks
    )
    assert all(chunk.token_count <= 800 for chunk in chunks)
    assert caplog.records == []
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_atomic_limit_is_inclusive_and_one_token_over_forces_warning_split() -> None:
    """Treating 800 as exclusive or permitting 801 breaks the approved boundary."""
    exact_raw = "- " + "x" * 795 + "\n"
    over_raw = "- " + "x" * 796 + "\n"

    exact_chunks = _chunker().split(
        _section(f"# H\n{exact_raw}"), parser_version="parser-v1"
    )
    over_chunks = _chunker().split(
        _section(f"# H\n{over_raw}"), parser_version="parser-v1"
    )

    assert len(exact_chunks) == 1
    assert exact_chunks[0].token_count == 800
    assert exact_chunks[0].warnings == ()
    assert [chunk.token_count for chunk in over_chunks] == [800, 3]
    assert "".join(chunk.raw_text for chunk in over_chunks) == over_raw
    assert all(chunk.warnings for chunk in over_chunks)


@pytest.mark.parametrize(
    "normal_block",
    [
        "```text\n" + "x" * 700 + "\n```\n",
        "<div>\n" + "x" * 700 + "\n</div>\n",
    ],
    ids=["fenced-code", "html"],
)
def test_fenced_code_and_html_use_normal_target_and_overlap(
    normal_block: str,
) -> None:
    """Misclassifying code or HTML as atomic would bypass normal overlap."""
    section = _section(f"# H\n{normal_block}")

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert len(chunks) == 2
    assert chunks[0].token_count == 400
    assert chunks[1].raw_text.startswith(chunks[0].raw_text[-64:])
    assert chunks[0].raw_text + chunks[1].raw_text[64:] == normal_block
    assert all(chunk.token_count <= 600 and not chunk.warnings for chunk in chunks)


def test_mixed_physical_endings_drive_actual_overlap_line_ranges() -> None:
    """Generic line splitting would miscount CRLF or Unicode separators."""
    unicode_separator = "\u2028"
    body = (
        "a" * 300
        + "\r\n"
        + "b" * 100
        + unicode_separator
        + "b" * 199
        + "\r"
        + "c" * 100
        + "\n"
    )
    section = _section(body)

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert [chunk.token_count for chunk in chunks] == [400, 368]
    assert [(chunk.line_start, chunk.line_end) for chunk in chunks] == [(1, 2), (2, 3)]
    assert chunks[1].raw_text.startswith(chunks[0].raw_text[-64:])
    assert chunks[0].raw_text + chunks[1].raw_text[64:] == body
    assert unicode_separator in chunks[1].raw_text


def test_duplicate_atomic_bodies_keep_lines_ordinals_and_distinct_hashes() -> None:
    """Locating excerpts by body text would map duplicates to the first source range."""
    table = "| h |\n|---|\n| " + "x" * 300 + " |\n"
    section = _section(f"# H\n{table}\nmiddle\n\n{table}")

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert [chunk.ordinal for chunk in chunks] == [0, 1, 2]
    assert chunks[0].raw_text == chunks[2].raw_text == table
    assert (chunks[0].line_start, chunks[0].line_end) == (2, 4)
    assert chunks[1].raw_text == "\nmiddle\n\n"
    assert (chunks[2].line_start, chunks[2].line_end) == (8, 10)
    assert chunks[0].chunk_hash != chunks[2].chunk_hash
    assert "".join(chunk.raw_text for chunk in chunks) == section.body


def test_chunk_hash_matches_literal_task2_and_config_identity_coordinates() -> None:
    """Dropping any approved hash coordinate breaks deterministic chunk reuse."""
    chunk = _chunker().split(_section("# A\nbody\n"), parser_version="parser-v1")[0]

    assert chunk.chunk_hash == (
        "8e4e1be62615e60660bdfee6418d0ff52bdc4ea6b1977a880b71f99aaf84bcd1"
    )
    changed_parser_chunk = _chunker().split(
        _section("# A\nbody\n"), parser_version="parser-v2"
    )[0]
    assert changed_parser_chunk.chunk_hash != chunk.chunk_hash


def test_constructor_rejects_nonconcrete_contract_dtos_and_missing_methods() -> None:
    """DTO subtypes or incomplete tokenizers could alter deterministic identity."""
    chunker_type = _chunker_type()
    descriptor_values = {
        "model_name": "model",
        "revision": "revision",
        "library_name": "library",
        "library_version": "1.0",
        "add_special_tokens": False,
    }

    invalid_arguments = (
        (object(), _descriptor(), ChunkConfig()),
        (
            _FakeTokenCounter(),
            _TokenizerDescriptorSubclass(**descriptor_values),
            ChunkConfig(),
        ),
        (_FakeTokenCounter(), _descriptor(), _ChunkConfigSubclass()),
    )
    for token_counter, descriptor, config in invalid_arguments:
        with pytest.raises(ValueError, match="^Invalid chunker input contract$"):
            chunker_type(token_counter, descriptor, config)


@pytest.mark.parametrize("parser_version", [None, "", " \t", _StrSubclass("v1")])
def test_split_rejects_invalid_section_or_parser_identity(
    parser_version: object,
) -> None:
    """Invalid parser identity must fail before producing reusable chunk hashes."""
    section = _section("# A\nbody\n")

    with pytest.raises(ValueError, match="^Invalid split input contract$"):
        _chunker().split(section, parser_version=parser_version)


def test_split_rejects_parsed_section_subtypes() -> None:
    """A section subtype must not bypass the approved immutable parser DTO."""
    section = _section("# A\nbody\n")
    section_subtype = _ParsedSectionSubclass(
        ordinal=section.ordinal,
        parent_ordinal=section.parent_ordinal,
        level=section.level,
        heading=section.heading,
        heading_path=section.heading_path,
        body=section.body,
        line_start=section.line_start,
        line_end=section.line_end,
        blocks=section.blocks,
    )

    with pytest.raises(ValueError, match="^Invalid split input contract$"):
        _chunker().split(section_subtype, parser_version="parser-v1")


@pytest.mark.parametrize(
    "encode_result",
    ["secret-source-text", (True,), (1.5,), iter((1,))],
    ids=["string", "boolean-token", "float-token", "non-sequence"],
)
def test_malformed_encode_results_fail_closed_without_source(
    encode_result: object,
) -> None:
    """Malformed token IDs must not leak encoded source through an exception."""
    with pytest.raises(ValueError) as caught:
        _chunker(_EncodeResultCounter(encode_result)).split(
            _section("# Secret\nbody\n"), parser_version="parser-v1"
        )

    assert str(caught.value) == "Token counter returned malformed data"
    assert caught.value.__cause__ is None
    assert "Secret" not in str(caught.value)


def _invalid_offset_results(text_length: int) -> tuple[object, ...]:
    valid = [(index, index + 1) for index in range(text_length)]
    too_short = tuple(valid[:-1])
    zero_length = tuple([(0, 0), *valid[1:]])
    overlapping = tuple([valid[0], (0, 2), *valid[2:]])
    out_of_bounds = tuple([*valid[:-1], (text_length - 1, text_length + 1)])
    list_span = tuple([[0, 1], *valid[1:]])
    return too_short, zero_length, overlapping, out_of_bounds, list_span


@pytest.mark.parametrize(
    "offset_result",
    _invalid_offset_results(601),
    ids=["unaligned", "zero-length", "overlap", "out-of-bounds", "non-tuple-span"],
)
def test_malformed_offset_results_fail_closed_without_source(
    offset_result: object,
) -> None:
    """Unsafe source offsets must never reach slicing or expose source content."""
    with pytest.raises(ValueError) as caught:
        _chunker(_OffsetResultCounter(offset_result)).split(
            _section("s" * 601), parser_version="parser-v1"
        )

    assert str(caught.value) == "Token counter returned malformed data"
    assert caught.value.__cause__ is None
    assert "s" * 20 not in str(caught.value)


def test_token_counter_exceptions_are_sanitized_without_chaining() -> None:
    """Adapter exception details may contain source and must stay internal."""
    with pytest.raises(ValueError) as caught:
        _chunker(_RaisingTokenCounter()).split(
            _section("# Secret\nbody\n"), parser_version="parser-v1"
        )

    assert str(caught.value) == "Token counter failed"
    assert caught.value.__cause__ is None
    assert "secret-tokenizer-detail" not in str(caught.value)


def _assert_sanitized_tokenizer_error(
    error: ValueError, *, expected_message: str
) -> None:
    rendered = f"{type(error).__name__}: {error!r}: {error}"
    assert str(error) == expected_message
    assert type(error) is ValueError
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "_SecretTokenizerException" not in rendered
    assert "_SecretAdapterError" not in rendered
    assert "secret-" not in rendered


def _assert_traceback_has_no_adapter_secret(error: ValueError) -> None:
    rendered = "".join(traceback.format_exception(error))
    assert "_SecretAdapterError" not in rendered
    assert "secret-" not in rendered


def test_custom_encode_exception_is_sanitized_without_context() -> None:
    """A custom adapter exception must not leak class, message, or context."""
    with pytest.raises(ValueError) as caught:
        _chunker(_EncodeFailureCounter(_SecretTokenizerException)).split(
            _section("# Secret\nbody\n"), parser_version="parser-v1"
        )

    _assert_sanitized_tokenizer_error(
        caught.value, expected_message="Token counter failed"
    )


def test_custom_offsets_exception_is_sanitized_without_context() -> None:
    """A custom offset exception must not expose source-bearing adapter details."""
    with pytest.raises(ValueError) as caught:
        _chunker(_OffsetFailureCounter(_SecretTokenizerException)).split(
            _section("s" * 601), parser_version="parser-v1"
        )

    _assert_sanitized_tokenizer_error(
        caught.value, expected_message="Token counter failed"
    )


def test_constructor_encode_property_exception_is_sanitized_without_context() -> None:
    """Inspecting an encode property must sanitize any ordinary exception."""
    with pytest.raises(ValueError) as caught:
        _chunker_type()(
            _EncodePropertyFailureCounter(_SecretTokenizerException),
            _descriptor(),
            ChunkConfig(),
        )

    _assert_sanitized_tokenizer_error(
        caught.value, expected_message="Invalid chunker input contract"
    )


def test_constructor_offsets_property_exception_is_sanitized_without_context() -> None:
    """Inspecting an offsets property must sanitize any ordinary exception."""
    with pytest.raises(ValueError) as caught:
        _chunker_type()(
            _OffsetPropertyFailureCounter(_SecretTokenizerException),
            _descriptor(),
            ChunkConfig(),
        )

    _assert_sanitized_tokenizer_error(
        caught.value, expected_message="Invalid chunker input contract"
    )


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize(
    "phase",
    ["constructor-encode", "constructor-offsets", "runtime-encode", "runtime-offsets"],
)
def test_non_exception_base_exceptions_are_never_sanitized(
    phase: str, error_type: type[BaseException]
) -> None:
    """Cancellation and process-control signals must cross every error boundary."""
    if phase == "constructor-encode":
        counter = _EncodePropertyFailureCounter(error_type)
        with pytest.raises(error_type):
            _chunker_type()(counter, _descriptor(), ChunkConfig())
        return
    if phase == "constructor-offsets":
        counter = _OffsetPropertyFailureCounter(error_type)
        with pytest.raises(error_type):
            _chunker_type()(counter, _descriptor(), ChunkConfig())
        return
    if phase == "runtime-encode":
        chunker = _chunker(_EncodeFailureCounter(error_type))
        with pytest.raises(error_type):
            chunker.split(_section("# A\nbody\n"), parser_version="parser-v1")
        return

    chunker = _chunker(_OffsetFailureCounter(error_type))
    with pytest.raises(error_type):
        chunker.split(_section("s" * 601), parser_version="parser-v1")


@pytest.mark.parametrize(
    ("side", "operation"),
    [
        ("encode", "len"),
        ("encode", "iter"),
        ("encode", "next"),
        ("encode", "getitem"),
        ("offsets", "len"),
        ("offsets", "iter"),
        ("offsets", "next"),
        ("offsets", "getitem"),
    ],
    ids=[
        "encode-len",
        "encode-iter",
        "encode-next",
        "encode-getitem",
        "offsets-len",
        "offsets-iter",
        "offsets-next",
        "offsets-getitem",
    ],
)
def test_adapter_sequence_exceptions_are_sanitized_without_traceback_leaks(
    side: str, operation: str
) -> None:
    """Consuming an adapter Sequence must not expose its ordinary failures."""
    error = _SecretAdapterError(f"secret-{side}-{operation}-detail")
    counter = _SequenceOperationFailureCounter(side, operation, error)
    source = "# Secret\nbody\n" if side == "encode" else "s" * 601
    caught_error: Exception | None = None

    try:
        _chunker(counter).split(_section(source), parser_version="parser-v1")
    except (_SecretAdapterError, ValueError) as raised:
        caught_error = raised
    else:
        pytest.fail("adapter Sequence failure was not raised")

    assert type(caught_error) is ValueError
    _assert_sanitized_tokenizer_error(
        caught_error, expected_message="Token counter failed"
    )
    _assert_traceback_has_no_adapter_secret(caught_error)


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize(
    ("side", "operation"),
    [
        ("encode", "len"),
        ("encode", "iter"),
        ("encode", "next"),
        ("encode", "getitem"),
        ("offsets", "len"),
        ("offsets", "iter"),
        ("offsets", "next"),
        ("offsets", "getitem"),
    ],
    ids=[
        "encode-len",
        "encode-iter",
        "encode-next",
        "encode-getitem",
        "offsets-len",
        "offsets-iter",
        "offsets-next",
        "offsets-getitem",
    ],
)
def test_adapter_sequence_base_exceptions_preserve_the_same_object(
    side: str,
    operation: str,
    error_type: type[BaseException],
) -> None:
    """Process-control signals must cross Sequence consumption unchanged."""
    error = error_type(f"secret-{side}-{operation}-detail")
    counter = _SequenceOperationFailureCounter(side, operation, error)
    source = "# A\nbody\n" if side == "encode" else "s" * 601

    with pytest.raises(error_type) as caught:
        _chunker(counter).split(_section(source), parser_version="parser-v1")

    assert caught.value is error


@pytest.mark.parametrize("side", ["encode", "offsets"])
@pytest.mark.parametrize(
    "mismatch",
    [
        "len-over",
        "len-under",
        "iterator-short",
        "iterator-long",
        "fallback-short",
        "fallback-long",
        "bounded-infinite",
    ],
)
def test_adapter_sequence_length_mismatches_fail_closed_with_bounded_iteration(
    side: str, mismatch: str
) -> None:
    """Trusting declared length or exhausting an iterator admits malformed data."""
    counter = _SequenceLengthMismatchCounter(side, mismatch)
    source = "# A\nbody\n" if side == "encode" else "s" * 601
    caught_error: Exception | None = None

    try:
        _chunker(counter).split(_section(source), parser_version="parser-v1")
    except (IndexError, _SecretAdapterError, ValueError) as raised:
        caught_error = raised
    else:
        pytest.fail("adapter Sequence length mismatch was not rejected")

    assert type(caught_error) is ValueError
    _assert_sanitized_tokenizer_error(
        caught_error,
        expected_message="Token counter returned malformed data",
    )
    _assert_traceback_has_no_adapter_secret(caught_error)
    if mismatch == "bounded-infinite":
        assert counter.infinite_sequence is not None
        assert counter.infinite_sequence.iterator.calls == (
            len(counter.infinite_sequence) + 1
        )


@pytest.mark.parametrize("side", ["encode", "offsets"])
@pytest.mark.parametrize("sequence_kind", ["exact-iterator", "exact-fallback"])
def test_adapter_sequence_exact_declared_length_preserves_split_behavior(
    side: str, sequence_kind: str
) -> None:
    """Exactly N items followed by StopIteration remain valid adapter output."""
    source = "# A\nbody\n" if side == "encode" else "s" * 601
    counter = _SequenceLengthMismatchCounter(side, sequence_kind)

    chunks = _chunker(counter).split(_section(source), parser_version="parser-v1")
    expected = _chunker().split(_section(source), parser_version="parser-v1")

    assert chunks == expected


@pytest.mark.parametrize("side", ["encode", "offsets"])
def test_adapter_huge_declared_length_is_rejected_without_sequence_consumption(
    side: str,
) -> None:
    """An attacker-declared token count must fail before iterator consumption."""
    counter = _HugeDeclaredLengthCounter(side)
    source = "# A\nbody\n" if side == "encode" else "s" * 601

    with pytest.raises(ValueError) as caught:
        _chunker(counter).split(_section(source), parser_version="parser-v1")

    _assert_sanitized_tokenizer_error(
        caught.value,
        expected_message="Token counter returned malformed data",
    )
    _assert_traceback_has_no_adapter_secret(caught.value)
    assert counter.result is not None
    assert counter.result.iter_calls == 0
    assert counter.result.iterator.next_calls == 0
    assert counter.result.getitem_calls == 0


@pytest.mark.parametrize("side", ["encode", "offsets"])
def test_adapter_declared_length_text_boundary_preserves_unicode_behavior(
    side: str,
) -> None:
    """Exactly one token per Unicode character remains a valid upper boundary."""
    source = "# 제목\n본문🙂\n" if side == "encode" else "한🙂" * 301
    counter = _DeclaredLengthCounter(side, extra_declared_items=0)

    chunks = _chunker(counter).split(_section(source), parser_version="parser-v1")
    expected = _chunker().split(_section(source), parser_version="parser-v1")

    assert chunks == expected
    assert counter.results


@pytest.mark.parametrize("side", ["encode", "offsets"])
def test_adapter_declared_length_one_over_text_is_rejected_without_consumption(
    side: str,
) -> None:
    """Even a one-item impossible declaration must fail before iteration."""
    source = "# 제목\n본문🙂\n" if side == "encode" else "한🙂" * 301
    counter = _DeclaredLengthCounter(side, extra_declared_items=1)

    with pytest.raises(ValueError, match="^Token counter returned malformed data$"):
        _chunker(counter).split(_section(source), parser_version="parser-v1")

    assert counter.results
    assert counter.results[0].iter_calls == 0
    assert counter.results[0].getitem_calls == 0


def test_chunk_contract_exposes_stable_version_and_default_config() -> None:
    """Chunking exposes the approved stable identity and token limits."""
    assert CHUNKER_VERSION == "parent-child-v1"
    assert ChunkConfig() == ChunkConfig(
        target_tokens=400,
        soft_max_tokens=600,
        overlap_tokens=64,
        atomic_max_tokens=800,
        parent_context_max_tokens=1200,
    )


def test_tokenizer_descriptor_accepts_only_exact_immutable_identity_values() -> None:
    """Tokenizer identity is immutable, slotted, and free of raw credentials."""
    descriptor = TokenizerDescriptor(
        model_name="Qwen/Qwen3-Embedding-0.6B",
        revision="0123456789abcdef",
        library_name="transformers",
        library_version="5.15.0",
        add_special_tokens=False,
    )

    assert descriptor.model_name == "Qwen/Qwen3-Embedding-0.6B"
    assert descriptor.revision == "0123456789abcdef"
    assert descriptor.library_name == "transformers"
    assert descriptor.library_version == "5.15.0"
    assert descriptor.add_special_tokens is False
    assert not hasattr(descriptor, "__dict__")
    with pytest.raises(FrozenInstanceError):
        descriptor.revision = "changed"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("model_name", ""),
        ("model_name", " \t"),
        ("model_name", _StrSubclass("model")),
        ("revision", ""),
        ("revision", "\n"),
        ("revision", _StrSubclass("revision")),
        ("library_name", ""),
        ("library_name", " "),
        ("library_name", _StrSubclass("library")),
        ("library_version", ""),
        ("library_version", "\t"),
        ("library_version", _StrSubclass("1.0")),
        ("add_special_tokens", 0),
        ("add_special_tokens", 1),
    ],
)
def test_tokenizer_descriptor_rejects_invalid_exact_values(
    field_name: str, invalid_value: object
) -> None:
    """Blank, subclassed, and non-boolean descriptor fields are rejected."""
    values = {
        "model_name": "model",
        "revision": "revision",
        "library_name": "library",
        "library_version": "1.0",
        "add_special_tokens": False,
    }
    values[field_name] = invalid_value

    with pytest.raises(ValueError):
        TokenizerDescriptor(**values)


def test_chunk_config_accepts_approved_boundary_values_and_is_immutable() -> None:
    """The smallest coherent token limits form an immutable configuration."""
    config = ChunkConfig(
        target_tokens=1,
        soft_max_tokens=1,
        overlap_tokens=0,
        atomic_max_tokens=1,
        parent_context_max_tokens=1,
    )

    assert config.target_tokens == 1
    assert not hasattr(config, "__dict__")
    with pytest.raises(FrozenInstanceError):
        config.target_tokens = 2


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("target_tokens", True),
        ("target_tokens", 1.0),
        ("target_tokens", _IntSubclass(400)),
        ("soft_max_tokens", False),
        ("soft_max_tokens", 600.0),
        ("soft_max_tokens", _IntSubclass(600)),
        ("overlap_tokens", True),
        ("overlap_tokens", 64.0),
        ("overlap_tokens", _IntSubclass(64)),
        ("atomic_max_tokens", False),
        ("atomic_max_tokens", 800.0),
        ("atomic_max_tokens", _IntSubclass(800)),
        ("parent_context_max_tokens", True),
        ("parent_context_max_tokens", 1200.0),
        ("parent_context_max_tokens", _IntSubclass(1200)),
    ],
)
def test_chunk_config_rejects_non_exact_integer_fields(
    field_name: str, invalid_value: object
) -> None:
    """Booleans, floats, and integer subclasses cannot enter config identity."""
    values = {
        "target_tokens": 400,
        "soft_max_tokens": 600,
        "overlap_tokens": 64,
        "atomic_max_tokens": 800,
        "parent_context_max_tokens": 1200,
    }
    values[field_name] = invalid_value

    with pytest.raises(ValueError):
        ChunkConfig(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_tokens": 0},
        {"soft_max_tokens": 0},
        {"overlap_tokens": -1},
        {"atomic_max_tokens": 0},
        {"parent_context_max_tokens": 0},
        {"target_tokens": 601},
        {"soft_max_tokens": 801},
        {"overlap_tokens": 400},
    ],
)
def test_chunk_config_rejects_incoherent_token_boundaries(
    overrides: dict[str, int],
) -> None:
    """Non-positive or incorrectly ordered token boundaries are rejected."""
    values = {
        "target_tokens": 400,
        "soft_max_tokens": 600,
        "overlap_tokens": 64,
        "atomic_max_tokens": 800,
        "parent_context_max_tokens": 1200,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        ChunkConfig(**values)


def test_chunk_config_identity_hash_matches_the_approved_exact_payload() -> None:
    """Chunk identity hashes only approved chunker and tokenizer coordinates."""
    config = ChunkConfig()
    descriptor = TokenizerDescriptor(
        model_name="Qwen/Qwen3-Embedding-0.6B",
        revision="0123456789abcdef",
        library_name="transformers",
        library_version="5.15.0",
        add_special_tokens=False,
    )

    digest = chunk_config_identity_hash(config, descriptor)

    assert digest == "8d792a133589cf9a85c2ecb963005d55d2b0950f7d5cfdb1b0cee405dd233395"
    assert re.fullmatch(r"[0-9a-f]{64}", digest) is not None
    with pytest.raises(TypeError):
        chunk_config_identity_hash(config, descriptor, parser_version="markdown-v1")


def test_chunk_config_identity_hash_is_deterministic() -> None:
    """Equivalent value objects always produce the same identity digest."""
    first_config = ChunkConfig()
    second_config = ChunkConfig()
    first_descriptor = TokenizerDescriptor(
        model_name="model",
        revision="revision",
        library_name="library",
        library_version="1.0",
        add_special_tokens=False,
    )
    second_descriptor = TokenizerDescriptor(
        model_name="model",
        revision="revision",
        library_name="library",
        library_version="1.0",
        add_special_tokens=False,
    )

    assert chunk_config_identity_hash(
        first_config, first_descriptor
    ) == chunk_config_identity_hash(second_config, second_descriptor)


def test_chunk_config_identity_hash_changes_with_every_config_field() -> None:
    """No approved numeric chunk setting can change without invalidating reuse."""
    config = ChunkConfig()
    descriptor = TokenizerDescriptor(
        model_name="model",
        revision="revision",
        library_name="library",
        library_version="1.0",
        add_special_tokens=False,
    )
    baseline = chunk_config_identity_hash(config, descriptor)

    changed_configs = (
        replace(config, target_tokens=401),
        replace(config, soft_max_tokens=601),
        replace(config, overlap_tokens=65),
        replace(config, atomic_max_tokens=801),
        replace(config, parent_context_max_tokens=1201),
    )

    assert all(
        chunk_config_identity_hash(changed_config, descriptor) != baseline
        for changed_config in changed_configs
    )


def test_chunk_config_identity_hash_changes_with_every_tokenizer_field() -> None:
    """No tokenizer behavior coordinate can change without invalidating reuse."""
    config = ChunkConfig()
    descriptor = TokenizerDescriptor(
        model_name="model",
        revision="revision",
        library_name="library",
        library_version="1.0",
        add_special_tokens=False,
    )
    baseline = chunk_config_identity_hash(config, descriptor)

    changed_descriptors = (
        replace(descriptor, model_name="changed-model"),
        replace(descriptor, revision="changed-revision"),
        replace(descriptor, library_name="changed-library"),
        replace(descriptor, library_version="2.0"),
        replace(descriptor, add_special_tokens=True),
    )

    assert all(
        chunk_config_identity_hash(config, changed_descriptor) != baseline
        for changed_descriptor in changed_descriptors
    )


def test_token_counter_protocol_describes_source_backed_offsets() -> None:
    """A structural counter supplies aligned token IDs and exact source offsets."""
    ports_module = import_module("omf_retrieval.application.indexing.ports")
    token_counter_type = getattr(ports_module, "TokenCounter", None)
    assert token_counter_type is not None
    assert callable(getattr(token_counter_type, "encode", None))
    assert callable(getattr(token_counter_type, "offsets", None))
    counter = _FakeTokenCounter()

    assert counter.encode("문서 A") == (47928, 49436, 32, 65)
    assert counter.offsets("문서 A") == ((0, 1), (1, 2), (2, 3), (3, 4))


def test_chunk_warning_is_stable_source_mapped_and_immutable() -> None:
    """An oversized atomic split warning keeps only stable safe metadata."""
    ports_module = import_module("omf_retrieval.application.indexing.ports")
    warning_type = getattr(ports_module, "ChunkWarning", None)
    assert warning_type is not None

    warning = warning_type(block_kind="table_row", line_start=7, line_end=9)

    assert warning.code == "oversized_atomic_unit_token_split"
    assert (warning.block_kind, warning.line_start, warning.line_end) == (
        "table_row",
        7,
        9,
    )
    assert not hasattr(warning, "__dict__")
    with pytest.raises(FrozenInstanceError):
        warning.line_end = 10


@pytest.mark.parametrize(
    "overrides",
    [
        {"code": "other"},
        {"code": _StrSubclass("oversized_atomic_unit_token_split")},
        {"block_kind": ""},
        {"block_kind": _StrSubclass("table_row")},
        {"line_start": True},
        {"line_start": _IntSubclass(7)},
        {"line_start": 0},
        {"line_end": 7.0},
        {"line_end": _IntSubclass(9)},
        {"line_end": 6},
    ],
)
def test_chunk_warning_rejects_unstable_or_invalid_metadata(
    overrides: dict[str, object],
) -> None:
    """Warning codes, kinds, and inclusive lines accept only exact values."""
    ports_module = import_module("omf_retrieval.application.indexing.ports")
    warning_type = getattr(ports_module, "ChunkWarning", None)
    assert warning_type is not None
    values = {"block_kind": "table_row", "line_start": 7, "line_end": 9}
    values.update(overrides)

    with pytest.raises(ValueError):
        warning_type(**values)


def test_chunk_draft_is_source_mapped_and_immutable() -> None:
    """A valid draft keeps deterministic coordinates and warning metadata."""
    ports_module = import_module("omf_retrieval.application.indexing.ports")
    warning_type = getattr(ports_module, "ChunkWarning", None)
    draft_type = getattr(ports_module, "ChunkDraft", None)
    assert warning_type is not None
    assert draft_type is not None
    warning = warning_type(block_kind="list_item", line_start=4, line_end=4)

    draft = draft_type(
        ordinal=0,
        raw_text="- 원문\n",
        search_text="제목\n- 원문",
        token_count=5,
        line_start=4,
        line_end=4,
        chunk_hash="a" * 64,
        warnings=(warning,),
    )

    assert draft.warnings == (warning,)
    assert draft.chunk_hash == "a" * 64
    assert not hasattr(draft, "__dict__")
    with pytest.raises(FrozenInstanceError):
        draft.ordinal = 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"ordinal": True},
        {"ordinal": _IntSubclass(0)},
        {"ordinal": -1},
        {"raw_text": ""},
        {"raw_text": _StrSubclass("원문")},
        {"search_text": ""},
        {"search_text": _StrSubclass("검색")},
        {"token_count": False},
        {"token_count": _IntSubclass(5)},
        {"token_count": 0},
        {"line_start": True},
        {"line_start": 0},
        {"line_end": 3},
        {"chunk_hash": "A" * 64},
        {"chunk_hash": _StrSubclass("a" * 64)},
        {"chunk_hash": "a" * 63},
        {"warnings": []},
        {"warnings": _TupleSubclass()},
        {"warnings": (object(),)},
    ],
)
def test_chunk_draft_rejects_invalid_exact_values(
    overrides: dict[str, object],
) -> None:
    """Draft identity, text, counts, lines, hash, and warnings are validated."""
    ports_module = import_module("omf_retrieval.application.indexing.ports")
    draft_type = getattr(ports_module, "ChunkDraft", None)
    assert draft_type is not None
    values = {
        "ordinal": 0,
        "raw_text": "원문",
        "search_text": "제목\n원문",
        "token_count": 5,
        "line_start": 4,
        "line_end": 4,
        "chunk_hash": "a" * 64,
        "warnings": (),
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        draft_type(**values)
