"""Unit tests for source-backed parent context construction."""

import traceback
from collections.abc import Iterator, Sequence
from dataclasses import FrozenInstanceError, replace
from importlib import import_module

import pytest

from omf_retrieval.application.indexing.ports import (
    ChunkConfig,
    ParentContext,
    ParsedMarkdown,
    ParsedSection,
)
from omf_retrieval.infrastructure.source.chunker import ParentContextBuilder
from omf_retrieval.infrastructure.source.markdown import MarkdownItParser


class _IntSubclass(int):
    """Exercise exact built-in integer validation."""


class _StrSubclass(str):
    """Exercise exact built-in string validation."""


class _ParsedSectionSubclass(ParsedSection):
    """Exercise the concrete section DTO boundary."""


class _ParsedMarkdownSubclass(ParsedMarkdown):
    """Exercise the concrete parser-result DTO boundary."""


class _ChunkConfigSubclass(ChunkConfig):
    """Exercise the concrete chunk-config DTO boundary."""


class _CharacterTokenCounter:
    """Map every source character to one deterministic token and offset."""

    def encode(self, text: str) -> tuple[int, ...]:
        return tuple(ord(character) for character in text)

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((index, index + 1) for index in range(len(text)))


class _PairTokenCounter:
    """Use two-character tokens to distinguish offsets from character counts."""

    def encode(self, text: str) -> tuple[int, ...]:
        return tuple(ord(text[index]) for index in range(0, len(text), 2))

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple(
            (index, min(index + 2, len(text))) for index in range(0, len(text), 2)
        )


class _RecordingParser:
    """Record exact source inputs while delegating to the real parser."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._parser = MarkdownItParser()

    def parse(self, source: str) -> ParsedMarkdown:
        self.calls.append(source)
        return self._parser.parse(source)


class _StaticParser:
    """Return a controlled parser result for output-contract tests."""

    def __init__(self, result: object) -> None:
        self._result = result

    def parse(self, source: str) -> object:
        return self._result


class _SecretDependencyError(Exception):
    """Represent an ordinary dependency exception containing private detail."""


class _ParserPropertyFailure:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    @property
    def parse(self) -> object:
        raise self._error


class _ParserMethodFailure:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def parse(self, source: str) -> object:
        raise self._error


class _ParserResultPropertyTrap:
    @property
    def parser_version(self) -> object:
        raise _SecretDependencyError("secret-parser-result-detail")


class _TokenPropertyFailure:
    def __init__(self, side: str, error: BaseException) -> None:
        self._side = side
        self._error = error

    @property
    def encode(self) -> object:
        if self._side == "encode":
            raise self._error
        return _CharacterTokenCounter().encode

    @property
    def offsets(self) -> object:
        if self._side == "offsets":
            raise self._error
        return _CharacterTokenCounter().offsets


class _TokenMethodFailure(_CharacterTokenCounter):
    def __init__(self, side: str, error: BaseException) -> None:
        self._side = side
        self._error = error

    def encode(self, text: str) -> tuple[int, ...]:
        if self._side == "encode":
            raise self._error
        return super().encode(text)

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        if self._side == "offsets":
            raise self._error
        return super().offsets(text)


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


class _SequenceFailureCounter(_CharacterTokenCounter):
    def __init__(self, side: str, operation: str, error: BaseException) -> None:
        self._side = side
        self._operation = operation
        self._error = error

    def encode(self, text: str) -> object:
        items = super().encode(text)
        if self._side == "encode":
            return _FailingSequence(items, self._operation, self._error)
        return items

    def offsets(self, text: str) -> object:
        items = super().offsets(text)
        if self._side == "offsets":
            return _FailingSequence(items, self._operation, self._error)
        return items


class _GuardedInfiniteIterator(Iterator[object]):
    def __init__(self, item: object) -> None:
        self.item = item
        self.next_count = 0

    def __next__(self) -> object:
        self.next_count += 1
        if self.next_count > 2:
            raise _SecretDependencyError("secret-unbounded-consumption")
        return self.item


class _GuardedInfiniteSequence(Sequence[object]):
    def __init__(self, item: object) -> None:
        self.iterator = _GuardedInfiniteIterator(item)

    def __len__(self) -> int:
        return 1

    def __iter__(self) -> Iterator[object]:
        return self.iterator

    def __getitem__(self, index: int | slice) -> object:
        return self.iterator.item


class _InfiniteResultCounter(_CharacterTokenCounter):
    def __init__(self, side: str) -> None:
        self._side = side
        self.result: _GuardedInfiniteSequence | None = None

    def encode(self, text: str) -> object:
        if self._side == "encode":
            self.result = _GuardedInfiniteSequence(1)
            return self.result
        return super().encode(text)

    def offsets(self, text: str) -> object:
        if self._side == "offsets":
            self.result = _GuardedInfiniteSequence((0, 1))
            return self.result
        return super().offsets(text)


class _MalformedCounter(_CharacterTokenCounter):
    def __init__(self, side: str, result: object) -> None:
        self._side = side
        self._result = result

    def encode(self, text: str) -> object:
        return self._result if self._side == "encode" else super().encode(text)

    def offsets(self, text: str) -> object:
        return self._result if self._side == "offsets" else super().offsets(text)


def _section(source: str, *, ordinal: int = -1) -> ParsedSection:
    parsed = MarkdownItParser().parse(source)
    return parsed.sections[ordinal]


def _builder(
    *,
    parser: object | None = None,
    token_counter: object | None = None,
    limit: int = 1200,
    config: ChunkConfig | None = None,
) -> ParentContextBuilder:
    return ParentContextBuilder(
        parser if parser is not None else MarkdownItParser(),
        token_counter if token_counter is not None else _CharacterTokenCounter(),
        config if config is not None else ChunkConfig(parent_context_max_tokens=limit),
    )


def test_parent_context_public_api_is_available() -> None:
    """Catch accidental omission of the approved parent-context API."""
    ports = import_module("omf_retrieval.application.indexing.ports")
    chunker = import_module("omf_retrieval.infrastructure.source.chunker")

    parent_context_type = getattr(ports, "ParentContext", None)
    builder_type = getattr(chunker, "ParentContextBuilder", None)

    assert parent_context_type is not None
    assert builder_type is not None

    context = parent_context_type(
        raw_text="source\n", token_count=7, line_start=3, line_end=3
    )
    assert context.raw_text == "source\n"
    assert context.token_count == 7
    assert context.line_start == 3
    assert context.line_end == 3
    assert context.__slots__ == ("raw_text", "token_count", "line_start", "line_end")
    with pytest.raises(FrozenInstanceError):
        context.token_count = 8


@pytest.mark.parametrize(
    ("changes", "valid"),
    [
        ({"raw_text": "x"}, True),
        ({"raw_text": ""}, False),
        ({"raw_text": _StrSubclass("x")}, False),
        ({"raw_text": None}, False),
        ({"token_count": 1}, True),
        ({"token_count": 0}, False),
        ({"token_count": -1}, False),
        ({"token_count": True}, False),
        ({"token_count": _IntSubclass(1)}, False),
        ({"line_start": 1, "line_end": 1}, True),
        ({"line_start": 0}, False),
        ({"line_start": True}, False),
        ({"line_start": _IntSubclass(1)}, False),
        ({"line_end": 1}, False),
        ({"line_end": True}, False),
        ({"line_end": _IntSubclass(2)}, False),
    ],
)
def test_parent_context_validates_exact_immutable_values(
    changes: dict[str, object], valid: bool
) -> None:
    """Catch empty, non-positive, non-inclusive, and subtype DTO values."""
    values: dict[str, object] = {
        "raw_text": "body\n",
        "token_count": 5,
        "line_start": 2,
        "line_end": 2,
    }
    values.update(changes)

    if valid:
        assert ParentContext(**values)  # type: ignore[arg-type]
    else:
        with pytest.raises(ValueError):
            ParentContext(**values)  # type: ignore[arg-type]


def test_short_section_reparses_body_and_returns_exact_source_context() -> None:
    """Catch heading injection, parser bypass, and relative line-range output."""
    section = _section("# Heading\nalpha\nbeta\n")
    parser = _RecordingParser()
    builder = _builder(parser=parser)
    build = getattr(builder, "build", None)

    assert build is not None
    first = build(
        section,
        matched_raw_text="alpha\nbeta\n",
        matched_line_start=2,
        matched_line_end=3,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )
    second = build(
        section,
        matched_raw_text="alpha\nbeta\n",
        matched_line_start=2,
        matched_line_end=3,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )

    assert parser.calls == [section.body, section.body]
    assert (
        first
        == second
        == ParentContext(
            raw_text="alpha\nbeta\n",
            token_count=11,
            line_start=2,
            line_end=3,
        )
    )
    assert "Heading" not in first.raw_text


def test_context_expands_nearest_blocks_and_prefers_preceding_on_a_tie() -> None:
    """Catch non-contiguous expansion and following-first tie behavior."""
    section = _section("# H\nprior\n\nmatch\n\nafter\n")

    context = _builder(limit=14).build(
        section,
        matched_raw_text="match\n",
        matched_line_start=4,
        matched_line_end=4,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )

    assert context == ParentContext(
        raw_text="prior\n\nmatch\n\n",
        token_count=14,
        line_start=2,
        line_end=5,
    )


def test_context_tries_following_block_when_preceding_block_does_not_fit() -> None:
    """Catch expansion that stops before trying the other adjacent side."""
    section = _section("# H\ntoo-long\n\nmatch\n\nafter\n")

    context = _builder(limit=14).build(
        section,
        matched_raw_text="match\n",
        matched_line_start=4,
        matched_line_end=4,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )

    assert context == ParentContext(
        raw_text="\nmatch\n\nafter\n",
        token_count=14,
        line_start=3,
        line_end=6,
    )


@pytest.mark.parametrize(
    ("source", "matched_line", "expected"),
    [
        ("# H\nmatch\n\nlater\n", 2, "match\n\nlater\n"),
        ("# H\nearlier\n\nmatch\n", 4, "earlier\n\nmatch\n"),
    ],
)
def test_context_expands_from_first_or_last_matched_block(
    source: str, matched_line: int, expected: str
) -> None:
    """Catch boundary indexing that drops first or last block context."""
    section = _section(source)

    context = _builder(limit=len(expected)).build(
        section,
        matched_raw_text="match\n",
        matched_line_start=matched_line,
        matched_line_end=matched_line,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )

    assert context.raw_text == expected
    assert context.token_count == len(expected)
    assert context.line_start == 2
    assert context.line_end == 4


def test_context_seeds_every_top_level_block_crossed_by_the_match() -> None:
    """Catch selection that uses only the first block of a discontiguous child."""
    section = _section("# H\none\n\ntwo\n\nthree\n")

    context = _builder(limit=9).build(
        section,
        matched_raw_text="one\n\ntwo\n",
        matched_line_start=2,
        matched_line_end=4,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )

    assert context == ParentContext(
        raw_text="one\n\ntwo\n",
        token_count=9,
        line_start=2,
        line_end=4,
    )


@pytest.mark.parametrize(
    ("block", "matched_start", "matched_end"),
    [
        ("| a | b |\n|---|---|\n| x | y |\n", 4, 6),
        ("- one\n- two\n", 4, 5),
        ("> one\n> two\n", 4, 5),
        ("```text\n# not a heading\n```\n", 4, 6),
        ("<div>\ninside\n</div>\n", 4, 6),
    ],
)
def test_context_preserves_parser_top_level_atomic_blocks(
    block: str, matched_start: int, matched_end: int
) -> None:
    """Catch context slicing inside table, list, quote, fence, or HTML blocks."""
    source = f"# H\nbefore\n\n{block}\nafter\n"
    section = _section(source)
    expected_block = block + ("\n" if block.startswith("-") else "")

    context = _builder(limit=len(expected_block)).build(
        section,
        matched_raw_text=block,
        matched_line_start=matched_start,
        matched_line_end=matched_end,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )

    assert context.raw_text == expected_block
    assert context.token_count == len(expected_block)
    assert context.line_start == matched_start
    assert context.line_end == matched_end + (1 if block.startswith("-") else 0)


def test_only_parent_limit_changes_context_selection() -> None:
    """Catch accidental use of child target, soft, overlap, or atomic settings."""
    section = _section("# H\nprior\n\nmatch\n\nafter\n")
    first_config = ChunkConfig(
        target_tokens=20,
        soft_max_tokens=30,
        overlap_tokens=2,
        atomic_max_tokens=40,
        parent_context_max_tokens=14,
    )
    second_config = ChunkConfig(
        target_tokens=200,
        soft_max_tokens=300,
        overlap_tokens=20,
        atomic_max_tokens=400,
        parent_context_max_tokens=14,
    )

    outputs = tuple(
        _builder(config=config).build(
            section,
            matched_raw_text="match\n",
            matched_line_start=4,
            matched_line_end=4,
            parser_version="markdown-it-py-4.2.0-omf-v1",
        )
        for config in (first_config, second_config)
    )

    assert outputs[0] == outputs[1]


def test_oversized_single_block_uses_offsets_around_unique_match() -> None:
    """Catch arbitrary prefix truncation that omits an interior matched child."""
    body = "abcdefghijMATCHklmnopqrst\n"
    section = _section(f"# H\n{body}")
    builder = _builder(token_counter=_PairTokenCounter(), limit=7)

    first = builder.build(
        section,
        matched_raw_text="MATCH",
        matched_line_start=2,
        matched_line_end=2,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )
    second = builder.build(
        section,
        matched_raw_text="MATCH",
        matched_line_start=2,
        matched_line_end=2,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )

    assert first == second
    assert first.raw_text in body
    assert "MATCH" in first.raw_text
    assert first.token_count <= 7
    assert first.line_start == first.line_end == 2


@pytest.mark.parametrize(
    ("body", "matched_raw_text", "limit"),
    [
        ("dup xx dup trailing\n", "dup", 5),
        ("no matching source here\n", "absent", 6),
        ("abcdefghijk\n", "abcdefgh", 7),
    ],
)
def test_oversized_block_rejects_ambiguous_missing_or_over_limit_match(
    body: str, matched_raw_text: str, limit: int
) -> None:
    """Catch unsafe offset selection without one bounded matched occurrence."""
    section = _section(f"# H\n{body}")

    with pytest.raises(ValueError) as caught:
        _builder(limit=limit).build(
            section,
            matched_raw_text=matched_raw_text,
            matched_line_start=2,
            matched_line_end=2,
            parser_version="markdown-it-py-4.2.0-omf-v1",
        )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert body.strip() not in str(caught.value)


def test_exact_parent_limit_returns_whole_body_and_one_over_uses_window() -> None:
    """Catch off-by-one handling at the approved 1,200-token boundary."""
    exact_body = "x" * 1199 + "\n"
    over_body = "x" * 597 + "MATCH" + "y" * 598 + "\n"
    exact_section = _section(f"# H\n{exact_body}")
    over_section = _section(f"# H\n{over_body}")
    builder = _builder()

    exact = builder.build(
        exact_section,
        matched_raw_text="x" * 10,
        matched_line_start=2,
        matched_line_end=2,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )
    over = builder.build(
        over_section,
        matched_raw_text="MATCH",
        matched_line_start=2,
        matched_line_end=2,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )

    assert exact.raw_text == exact_body
    assert exact.token_count == 1200
    assert over.raw_text in over_body
    assert "MATCH" in over.raw_text
    assert over.token_count == 1200


@pytest.mark.parametrize("line_ending", ["\n", "\r", "\r\n"])
def test_oversized_window_preserves_physical_line_endings(line_ending: str) -> None:
    """Catch normalized source bytes or incorrect CR/LF/CRLF line mapping."""
    block = line_ending.join(("```text", "a" * 12, "MATCH", "b" * 12, "```", ""))
    section = _section(f"# H{line_ending}{block}")

    context = _builder(limit=12).build(
        section,
        matched_raw_text="MATCH",
        matched_line_start=4,
        matched_line_end=4,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )

    assert context.raw_text in block
    assert "MATCH" in context.raw_text
    assert context.token_count <= 12
    assert 2 <= context.line_start <= 4 <= context.line_end <= 6


@pytest.mark.parametrize("separator", ["\u2028", "\u0085", "\v", "\f"])
def test_unicode_and_control_separators_remain_on_one_physical_line(
    separator: str,
) -> None:
    """Catch treating non-CR/LF characters as source line boundaries."""
    body = f"left{separator}MATCH{separator}right\n"
    section = _section(f"# H\n{body}")

    context = _builder(limit=len(body)).build(
        section,
        matched_raw_text="MATCH",
        matched_line_start=2,
        matched_line_end=2,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )

    assert context.raw_text == body
    assert context.line_start == context.line_end == 2


def _build_with(
    builder: ParentContextBuilder, *, oversized: bool = False
) -> ParentContext:
    body = "0123456789MATCHabcdefghij\n" if oversized else "body\n"
    return builder.build(
        _section(f"# H\n{body}"),
        matched_raw_text="MATCH" if oversized else "body\n",
        matched_line_start=2,
        matched_line_end=2,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )


def _assert_sanitized(error: BaseException) -> None:
    rendered = "".join(traceback.format_exception(error))
    assert type(error) is ValueError
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "secret-" not in str(error)
    assert "_SecretDependencyError" not in rendered
    assert "secret-dependency-detail" not in rendered


def test_setext_section_uses_derived_absolute_body_start() -> None:
    """Catch assuming every stored section heading occupies one physical line."""
    section = _section("Title\n=====\nbody\n")

    context = _builder().build(
        section,
        matched_raw_text="body\n",
        matched_line_start=3,
        matched_line_end=3,
        parser_version="markdown-it-py-4.2.0-omf-v1",
    )

    assert context.line_start == context.line_end == 3
    assert context.raw_text == "body\n"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("matched_raw_text", ""),
        ("matched_raw_text", _StrSubclass("body\n")),
        ("matched_raw_text", None),
        ("matched_line_start", 0),
        ("matched_line_start", True),
        ("matched_line_start", _IntSubclass(2)),
        ("matched_line_end", 1),
        ("matched_line_end", True),
        ("matched_line_end", _IntSubclass(2)),
        ("parser_version", ""),
        ("parser_version", " "),
        ("parser_version", _StrSubclass("markdown-it-py-4.2.0-omf-v1")),
        ("parser_version", None),
    ],
)
def test_build_rejects_non_exact_or_invalid_scalar_inputs(
    field: str, value: object
) -> None:
    """Catch coercion or ambiguous source coordinates at the public boundary."""
    inputs: dict[str, object] = {
        "matched_raw_text": "body\n",
        "matched_line_start": 2,
        "matched_line_end": 2,
        "parser_version": "markdown-it-py-4.2.0-omf-v1",
    }
    inputs[field] = value

    with pytest.raises(ValueError):
        _builder().build(_section("# H\nbody\n"), **inputs)  # type: ignore[arg-type]


def test_build_rejects_section_subtype_and_malformed_body_coordinates() -> None:
    """Catch accepting non-concrete DTOs or deriving a body before its section."""
    section = _section("# H\nbody\n")
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
    malformed = ParsedSection(
        ordinal=0,
        parent_ordinal=None,
        level=0,
        heading=None,
        heading_path=(),
        body="one\ntwo\nthree\n",
        line_start=1,
        line_end=2,
        blocks=(),
    )

    for candidate, line_start, line_end, raw_text in (
        (section_subtype, 2, 2, "body\n"),
        (malformed, 1, 2, "one\ntwo\n"),
    ):
        with pytest.raises(ValueError):
            _builder().build(
                candidate,
                matched_raw_text=raw_text,
                matched_line_start=line_start,
                matched_line_end=line_end,
                parser_version="markdown-it-py-4.2.0-omf-v1",
            )


@pytest.mark.parametrize(
    ("line_start", "line_end", "raw_text"),
    [
        (1, 1, "body\n"),
        (2, 3, "body\n"),
        (2, 2, "absent"),
    ],
)
def test_build_rejects_match_outside_derived_body_or_declared_line_slice(
    line_start: int, line_end: int, raw_text: str
) -> None:
    """Catch evidence coordinates that cannot reproduce the matched source."""
    with pytest.raises(ValueError):
        _builder().build(
            _section("# H\nbody\n"),
            matched_raw_text=raw_text,
            matched_line_start=line_start,
            matched_line_end=line_end,
            parser_version="markdown-it-py-4.2.0-omf-v1",
        )


def test_build_rejects_unexpected_heading_in_stored_body() -> None:
    """Catch treating nested parser sections as one stored parent body."""
    section = ParsedSection(
        ordinal=0,
        parent_ordinal=None,
        level=0,
        heading=None,
        heading_path=(),
        body="# unexpected\nbody\n",
        line_start=1,
        line_end=2,
        blocks=(),
    )

    with pytest.raises(ValueError):
        _builder().build(
            section,
            matched_raw_text="body\n",
            matched_line_start=2,
            matched_line_end=2,
            parser_version="markdown-it-py-4.2.0-omf-v1",
        )


def test_build_requires_exact_expected_parser_version() -> None:
    """Catch silently using parser output from a different behavior revision."""
    with pytest.raises(ValueError):
        _builder().build(
            _section("# H\nbody\n"),
            matched_raw_text="body\n",
            matched_line_start=2,
            matched_line_end=2,
            parser_version="different-parser-v2",
        )


def test_build_rejects_blank_version_even_when_parser_reports_it() -> None:
    """Catch accepting a whitespace-only parser identity on both boundaries."""
    section = _section("# H\nbody\n")
    parsed = MarkdownItParser().parse(section.body)
    blank_version_result = replace(parsed, parser_version=" ")

    with pytest.raises(ValueError):
        _builder(parser=_StaticParser(blank_version_result)).build(
            section,
            matched_raw_text="body\n",
            matched_line_start=2,
            matched_line_end=2,
            parser_version=" ",
        )


def test_builder_requires_protocol_methods_and_exact_config() -> None:
    """Catch accepting missing parser/tokenizer methods or a config subtype."""
    config = ChunkConfig()
    config_subtype = _ChunkConfigSubclass(
        target_tokens=config.target_tokens,
        soft_max_tokens=config.soft_max_tokens,
        overlap_tokens=config.overlap_tokens,
        atomic_max_tokens=config.atomic_max_tokens,
        parent_context_max_tokens=config.parent_context_max_tokens,
    )

    for parser, token_counter, candidate_config in (
        (None, _CharacterTokenCounter(), config),
        (MarkdownItParser(), None, config),
        (MarkdownItParser(), _CharacterTokenCounter(), config_subtype),
    ):
        with pytest.raises(ValueError):
            ParentContextBuilder(parser, token_counter, candidate_config)  # type: ignore[arg-type]


def test_build_rejects_non_exact_and_malformed_parser_results() -> None:
    """Catch parser substitutes, missing roots, body drift, and block-map drift."""
    section = _section("# H\none\n\ntwo\n")
    valid = MarkdownItParser().parse(section.body)
    root = valid.sections[0]
    subclass = _ParsedMarkdownSubclass(
        parser_version=valid.parser_version,
        sections=valid.sections,
    )
    reordered_root = replace(root, blocks=tuple(reversed(root.blocks)))
    dropped_root = replace(root, blocks=root.blocks[:1] + root.blocks[2:])
    tampered_block = replace(root.blocks[0], raw_text="tampered\n")
    tampered_root = replace(root, blocks=(tampered_block, *root.blocks[1:]))
    malformed_results: tuple[object, ...] = (
        None,
        _ParserResultPropertyTrap(),
        subclass,
        ParsedMarkdown(parser_version=valid.parser_version, sections=()),
        MarkdownItParser().parse("other\n"),
        MarkdownItParser().parse("# nested\nbody\n"),
        replace(valid, sections=(reordered_root,)),
        replace(valid, sections=(dropped_root,)),
        replace(valid, sections=(tampered_root,)),
    )

    for result in malformed_results:
        with pytest.raises(ValueError) as caught:
            _builder(parser=_StaticParser(result)).build(
                section,
                matched_raw_text="one\n",
                matched_line_start=2,
                matched_line_end=2,
                parser_version="markdown-it-py-4.2.0-omf-v1",
            )
        assert "secret-parser-result-detail" not in str(caught.value)


@pytest.mark.parametrize("stage", ["property", "method"])
def test_parser_ordinary_failures_are_sanitized(stage: str) -> None:
    """Catch parser exception type, message, cause, context, or traceback leaks."""
    error = _SecretDependencyError("secret-dependency-detail")

    with pytest.raises(ValueError) as caught:
        if stage == "property":
            _builder(parser=_ParserPropertyFailure(error))
        else:
            _build_with(_builder(parser=_ParserMethodFailure(error)))

    _assert_sanitized(caught.value)


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("stage", ["property", "method"])
def test_parser_process_control_signals_propagate(
    error_type: type[BaseException], stage: str
) -> None:
    """Catch swallowing interpreter-level parser control signals."""
    error = error_type()

    with pytest.raises(error_type) as caught:
        if stage == "property":
            _builder(parser=_ParserPropertyFailure(error))
        else:
            _build_with(_builder(parser=_ParserMethodFailure(error)))

    assert caught.value is error


@pytest.mark.parametrize("side", ["encode", "offsets"])
def test_tokenizer_property_ordinary_failures_are_sanitized(side: str) -> None:
    """Catch tokenizer property exception details escaping construction."""
    error = _SecretDependencyError("secret-dependency-detail")

    with pytest.raises(ValueError) as caught:
        _builder(token_counter=_TokenPropertyFailure(side, error))

    _assert_sanitized(caught.value)


@pytest.mark.parametrize("side", ["encode", "offsets"])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_tokenizer_property_process_control_signals_propagate(
    side: str, error_type: type[BaseException]
) -> None:
    """Catch swallowing interpreter-level tokenizer property signals."""
    error = error_type()

    with pytest.raises(error_type) as caught:
        _builder(token_counter=_TokenPropertyFailure(side, error))

    assert caught.value is error


@pytest.mark.parametrize("side", ["encode", "offsets"])
def test_tokenizer_method_ordinary_failures_are_sanitized(side: str) -> None:
    """Catch tokenizer call exception details escaping context construction."""
    error = _SecretDependencyError("secret-dependency-detail")
    builder = _builder(
        token_counter=_TokenMethodFailure(side, error),
        limit=5 if side == "offsets" else 1200,
    )

    with pytest.raises(ValueError) as caught:
        _build_with(builder, oversized=side == "offsets")

    _assert_sanitized(caught.value)


@pytest.mark.parametrize("side", ["encode", "offsets"])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_tokenizer_method_process_control_signals_propagate(
    side: str, error_type: type[BaseException]
) -> None:
    """Catch swallowing interpreter-level tokenizer method signals."""
    error = error_type()
    builder = _builder(
        token_counter=_TokenMethodFailure(side, error),
        limit=5 if side == "offsets" else 1200,
    )

    with pytest.raises(error_type) as caught:
        _build_with(builder, oversized=side == "offsets")

    assert caught.value is error


@pytest.mark.parametrize("side", ["encode", "offsets"])
@pytest.mark.parametrize("operation", ["len", "iter", "next", "getitem"])
def test_tokenizer_sequence_ordinary_failures_are_sanitized(
    side: str, operation: str
) -> None:
    """Catch adapter Sequence consumption failures leaking private detail."""
    error = _SecretDependencyError("secret-dependency-detail")
    builder = _builder(
        token_counter=_SequenceFailureCounter(side, operation, error),
        limit=5 if side == "offsets" else 1200,
    )

    with pytest.raises(ValueError) as caught:
        _build_with(builder, oversized=side == "offsets")

    _assert_sanitized(caught.value)


@pytest.mark.parametrize("side", ["encode", "offsets"])
@pytest.mark.parametrize("operation", ["len", "iter", "next", "getitem"])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_tokenizer_sequence_process_control_signals_propagate(
    side: str, operation: str, error_type: type[BaseException]
) -> None:
    """Catch swallowing control signals during bounded Sequence consumption."""
    error = error_type()
    builder = _builder(
        token_counter=_SequenceFailureCounter(side, operation, error),
        limit=5 if side == "offsets" else 1200,
    )

    with pytest.raises(error_type) as caught:
        _build_with(builder, oversized=side == "offsets")

    assert caught.value is error


@pytest.mark.parametrize("side", ["encode", "offsets"])
def test_tokenizer_sequence_materialization_is_bounded(side: str) -> None:
    """Catch unbounded iteration of a dishonest finite-length adapter result."""
    counter = _InfiniteResultCounter(side)
    builder = _builder(
        token_counter=counter,
        limit=5 if side == "offsets" else 1200,
    )

    with pytest.raises(ValueError):
        _build_with(builder, oversized=side == "offsets")

    assert counter.result is not None
    assert counter.result.iterator.next_count == 2


@pytest.mark.parametrize(
    ("side", "result"),
    [
        ("encode", object()),
        ("encode", "tokens"),
        ("encode", ()),
        ("encode", (True,)),
        ("offsets", object()),
        ("offsets", "offsets"),
        ("offsets", ()),
        ("offsets", ((0, 1),)),
        (
            "offsets",
            ((0, 1), (0, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7)),
        ),
        (
            "offsets",
            ((0, 0), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7)),
        ),
        (
            "offsets",
            ((True, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7)),
        ),
        (
            "offsets",
            ((0, 99), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7)),
        ),
    ],
)
def test_tokenizer_malformed_results_fail_closed(side: str, result: object) -> None:
    """Catch malformed token IDs, lengths, offset types, order, and ranges."""
    builder = _builder(
        token_counter=_MalformedCounter(side, result),
        limit=5 if side == "offsets" else 1200,
    )

    with pytest.raises(ValueError):
        if side == "offsets":
            builder.build(
                _section("# H\naaMbbb\n"),
                matched_raw_text="M",
                matched_line_start=2,
                matched_line_end=2,
                parser_version="markdown-it-py-4.2.0-omf-v1",
            )
        else:
            _build_with(builder)
