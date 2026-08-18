"""Unit tests for deterministic Markdown structure parsing."""

from dataclasses import FrozenInstanceError
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from types import ModuleType

import pytest

from omf_retrieval.application.indexing import ports

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BLOCK_STRUCTURE_FIXTURE = PROJECT_ROOT / "tests/fixtures/markdown/block-structure.md"


class StringSubclass(str):
    """Represent an invalid string subclass at an exact-type boundary."""


class TupleSubclass(tuple[object, ...]):
    """Represent an invalid tuple subclass at an exact-type boundary."""


class ParsedBlockSubclass(ports.ParsedBlock):
    """Represent an invalid parsed-block subclass in a child tuple."""


class ParsedSectionSubclass(ports.ParsedSection):
    """Represent an invalid parsed-section subclass in a document tuple."""


def _markdown_module() -> ModuleType:
    module_name = "omf_retrieval.infrastructure.source.markdown"
    assert find_spec(module_name) is not None, "Markdown parser module must exist"
    return import_module(module_name)


def test_parser_preserves_heading_hierarchy_and_lines() -> None:
    """Removing heading-stack or line-map logic breaks the parser contract."""
    markdown = _markdown_module()
    parser = markdown.MarkdownItParser()

    parsed = parser.parse("# A\nintro\n\n## B\nbody\n")

    assert type(parsed) is ports.ParsedMarkdown
    assert parsed.parser_version == "markdown-it-py-4.2.0-omf-v1"
    assert len(parsed.sections) == 2
    assert parsed.sections[1].heading_path == ("A", "B")
    assert parsed.sections[1].parent_ordinal == 0
    assert (parsed.sections[1].line_start, parsed.sections[1].line_end) == (4, 5)


@pytest.mark.parametrize("source", ["", "\n", " \t\r\n\n"])
def test_empty_or_blank_markdown_has_no_sections(source: str) -> None:
    """Blank-only input must not invent a synthetic source section."""
    parsed = _markdown_module().MarkdownItParser().parse(source)

    assert parsed.sections == ()


def test_nonempty_preamble_and_headingless_document_use_synthetic_roots() -> None:
    """Dropping non-heading source text loses evidence before the first heading."""
    parser = _markdown_module().MarkdownItParser()

    with_heading = parser.parse("preamble\n\n# A\nbody\n")
    without_heading = parser.parse("alpha\n\nbeta\n")

    assert [
        (
            section.ordinal,
            section.parent_ordinal,
            section.level,
            section.heading,
            section.heading_path,
            section.body,
            section.line_start,
            section.line_end,
        )
        for section in with_heading.sections
    ] == [
        (0, None, 0, None, (), "preamble\n\n", 1, 2),
        (1, None, 1, "A", ("A",), "body\n", 3, 4),
    ]
    assert [
        (
            section.ordinal,
            section.parent_ordinal,
            section.level,
            section.heading,
            section.heading_path,
            section.body,
            section.line_start,
            section.line_end,
        )
        for section in without_heading.sections
    ] == [(0, None, 0, None, (), "alpha\n\nbeta\n", 1, 3)]


def test_heading_only_and_setext_sections_preserve_source_boundaries() -> None:
    """Heading syntax must not leak markers or consume a neighboring section."""
    parser = _markdown_module().MarkdownItParser()

    heading_only = parser.parse("# Only\n")
    mixed = parser.parse("# A #\nbody\n\nSetext\n------\ntail\n")

    assert (
        heading_only.sections[0].heading,
        heading_only.sections[0].body,
        heading_only.sections[0].line_start,
        heading_only.sections[0].line_end,
    ) == ("Only", "", 1, 1)
    assert [
        (
            section.level,
            section.heading,
            section.heading_path,
            section.body,
            section.line_start,
            section.line_end,
        )
        for section in mixed.sections
    ] == [
        (1, "A", ("A",), "body\n\n", 1, 3),
        (2, "Setext", ("A", "Setext"), "tail\n", 4, 6),
    ]


def test_heading_stack_handles_skips_decreases_and_repeated_text() -> None:
    """Parentage depends on heading levels and ordinals, never heading uniqueness."""
    source = "# Same\n### Same\n## B\n#### Same\n# Same\n"

    parsed = _markdown_module().MarkdownItParser().parse(source)

    assert [
        (
            section.ordinal,
            section.parent_ordinal,
            section.level,
            section.heading_path,
            section.line_start,
            section.line_end,
        )
        for section in parsed.sections
    ] == [
        (0, None, 1, ("Same",), 1, 1),
        (1, 0, 3, ("Same", "Same"), 2, 2),
        (2, 0, 2, ("Same", "B"), 3, 3),
        (3, 2, 4, ("Same", "B", "Same"), 4, 4),
        (4, None, 1, ("Same",), 5, 5),
    ]


def test_nested_heading_syntax_does_not_create_sections() -> None:
    """Container and code content that looks like a heading stays section body."""
    source = BLOCK_STRUCTURE_FIXTURE.read_text(encoding="utf-8")

    parsed = _markdown_module().MarkdownItParser().parse(source)

    assert [
        (section.heading, section.heading_path, section.line_start, section.line_end)
        for section in parsed.sections
    ] == [
        ("Visible", ("Visible",), 1, 21),
        ("End", ("Visible", "End"), 22, 23),
    ]


def test_block_tree_preserves_rows_items_quote_children_and_raw_body() -> None:
    """Removing mapped child blocks forces Task 6 to reparse Markdown source."""
    source = BLOCK_STRUCTURE_FIXTURE.read_text(encoding="utf-8")

    section = _markdown_module().MarkdownItParser().parse(source).sections[0]
    table = _only_block(section.blocks, "table")
    bullet_list = _only_block(section.blocks, "bullet_list")
    blockquote = _only_block(section.blocks, "blockquote")

    assert "".join(block.raw_text for block in section.blocks) == section.body
    assert [
        (row.raw_text, row.line_start, row.line_end)
        for row in _descendants(table, "table_row")
    ] == [
        ("| A | B |\n", 18, 18),
        ("| x | y |\n", 20, 20),
    ]
    assert [
        (item.raw_text, item.line_start, item.line_end)
        for item in bullet_list.children
        if item.kind == "list_item"
    ] == [
        ("- # listed\n", 14, 14),
        ("- second\n  > nested quote\n\n", 15, 17),
    ]
    assert [
        (child.raw_text, child.line_start, child.line_end)
        for child in _descendants(blockquote, "paragraph")
    ] == [("> quoted body\n", 12, 12)]
    assert [
        (child.raw_text, child.line_start, child.line_end)
        for child in _descendants(bullet_list, "blockquote")
    ] == [("  > nested quote\n", 16, 16)]


def _only_block(blocks: tuple[ports.ParsedBlock, ...], kind: str) -> ports.ParsedBlock:
    matches = tuple(block for block in blocks if block.kind == kind)
    assert len(matches) == 1
    return matches[0]


def _descendants(block: ports.ParsedBlock, kind: str) -> tuple[ports.ParsedBlock, ...]:
    matches: list[ports.ParsedBlock] = []
    pending = list(reversed(block.children))
    while pending:
        child = pending.pop()
        if child.kind == kind:
            matches.append(child)
        pending.extend(reversed(child.children))
    return tuple(matches)


def test_parser_preserves_crlf_final_newline_unicode_and_inline_markup() -> None:
    """Normalizing line endings or inline source would corrupt later excerpts."""
    source = (
        "# **한글** `code`\r\n"
        "문장 뒤 공백  \r\n"
        "\r\n"
        "| 열 | 값 |\r\n"
        "|---|---|\r\n"
        "| 가 | 나 |\r\n"
    )

    section = _markdown_module().MarkdownItParser().parse(source).sections[0]

    assert section.heading == "**한글** `code`"
    assert section.body == source.split("\r\n", 1)[1]
    assert "".join(block.raw_text for block in section.blocks) == section.body
    assert section.blocks[-1].raw_text.endswith("\r\n")


def test_parser_preserves_final_line_without_newline_and_is_deterministic() -> None:
    """A final non-newline line must remain byte-for-byte stable across parses."""
    source = "# 제목\n마지막 공백  "
    parser = _markdown_module().MarkdownItParser()

    first = parser.parse(source)
    second = parser.parse(source)

    assert first == second
    assert first.sections[0].body == "마지막 공백  "
    assert (
        "".join(block.raw_text for block in first.sections[0].blocks) == "마지막 공백  "
    )


def test_parsed_values_are_frozen_slotted_and_recursively_immutable() -> None:
    """Mutable parser output could make indexed coordinates nondeterministic."""
    child = ports.ParsedBlock("paragraph", "body\n", 2, 2, ())
    block = ports.ParsedBlock("blockquote", "> body\n", 2, 2, (child,))
    section = ports.ParsedSection(
        ordinal=0,
        parent_ordinal=None,
        level=1,
        heading="A",
        heading_path=("A",),
        body="> body\n",
        line_start=1,
        line_end=2,
        blocks=(block,),
    )
    parsed = ports.ParsedMarkdown("parser-v1", (section,))

    assert not hasattr(block, "__dict__")
    assert not hasattr(section, "__dict__")
    assert not hasattr(parsed, "__dict__")
    assert type(parsed.sections) is tuple
    assert type(parsed.sections[0].blocks) is tuple
    assert type(parsed.sections[0].blocks[0].children) is tuple
    with pytest.raises(FrozenInstanceError):
        block.kind = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        section.body = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        parsed.sections = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: ports.ParsedBlock(StringSubclass("paragraph"), "x", 1, 1, ()),
        lambda: ports.ParsedBlock("paragraph", StringSubclass("x"), 1, 1, ()),
        lambda: ports.ParsedBlock("paragraph", "x", True, 1, ()),
        lambda: ports.ParsedBlock("paragraph", "x", 0, 1, ()),
        lambda: ports.ParsedBlock("paragraph", "x", 2, 1, ()),
        lambda: ports.ParsedBlock("paragraph", "x", 1, 1, []),
        lambda: ports.ParsedBlock(
            "blockquote",
            "> x",
            2,
            2,
            (ports.ParsedBlock("paragraph", "x", 1, 1, ()),),
        ),
        lambda: ports.ParsedBlock(
            "blockquote",
            "> x",
            1,
            1,
            (ParsedBlockSubclass("paragraph", "x", 1, 1, ()),),
        ),
    ],
)
def test_parsed_block_rejects_invalid_exact_types_and_ranges(
    constructor: object,
) -> None:
    """Malformed child coordinates must fail before reaching the indexer."""
    with pytest.raises(ValueError):
        constructor()  # type: ignore[operator]


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: _section(ordinal=True),
        lambda: _section(ordinal=-1),
        lambda: _section(parent_ordinal=True),
        lambda: _section(parent_ordinal=0),
        lambda: _section(level=True),
        lambda: _section(level=7),
        lambda: _section(heading=None),
        lambda: _section(heading_path=["A"]),
        lambda: _section(heading_path=(StringSubclass("A"),)),
        lambda: _section(body=StringSubclass("")),
        lambda: _section(line_start=0),
        lambda: _section(line_start=2, line_end=1),
        lambda: _section(blocks=[]),
        lambda: _section(blocks=(ports.ParsedBlock("paragraph", "outside", 2, 2, ()),)),
        lambda: ports.ParsedSection(0, None, 0, "root", (), "x", 1, 1, ()),
        lambda: ports.ParsedSection(0, None, 0, None, ("root",), "x", 1, 1, ()),
    ],
)
def test_parsed_section_rejects_invalid_exact_types_and_ranges(
    constructor: object,
) -> None:
    """Malformed section hierarchy and line coordinates fail closed."""
    with pytest.raises(ValueError):
        constructor()  # type: ignore[operator]


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: ports.ParsedMarkdown(StringSubclass("parser-v1"), ()),
        lambda: ports.ParsedMarkdown("parser-v1", []),
        lambda: ports.ParsedMarkdown(
            "parser-v1", (ParsedSectionSubclass(0, None, 1, "A", ("A",), "", 1, 1, ()),)
        ),
        lambda: ports.ParsedMarkdown("parser-v1", (_section(ordinal=1),)),
    ],
)
def test_parsed_markdown_rejects_invalid_exact_types_and_ordinals(
    constructor: object,
) -> None:
    """A document result accepts only exact immutable sequential sections."""
    with pytest.raises(ValueError):
        constructor()  # type: ignore[operator]


def test_parse_rejects_non_exact_string_without_exposing_source() -> None:
    """Invalid input errors must not echo potentially sensitive document text."""
    secret_source = StringSubclass("OMF-SECRET-SOURCE")

    with pytest.raises(ValueError) as error:
        _markdown_module().MarkdownItParser().parse(secret_source)

    assert str(secret_source) not in str(error.value)


def test_deep_heading_sequence_terminates_without_recursion_and_is_stable() -> None:
    """Many hierarchy transitions must use bounded stack depth and stable output."""
    source = "".join(
        f"{'#' * ((ordinal % 6) + 1)} H{ordinal}\n" for ordinal in range(1_500)
    )
    parser = _markdown_module().MarkdownItParser()

    try:
        first = parser.parse(source)
        second = parser.parse(source)
    except RecursionError:
        pytest.fail("heading hierarchy parsing must not depend on document depth")

    assert first == second
    assert len(first.sections) == 1_500
    assert first.sections[-1].ordinal == 1_499
    assert first.sections[-1].line_start == 1_500


def _section(**overrides: object) -> ports.ParsedSection:
    values: dict[str, object] = {
        "ordinal": 0,
        "parent_ordinal": None,
        "level": 1,
        "heading": "A",
        "heading_path": ("A",),
        "body": "",
        "line_start": 1,
        "line_end": 1,
        "blocks": (),
    }
    values.update(overrides)
    return ports.ParsedSection(**values)  # type: ignore[arg-type]
