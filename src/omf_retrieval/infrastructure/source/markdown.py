"""Parse Markdown headings and blocks into immutable source maps."""

from dataclasses import dataclass, field, replace

from markdown_it import MarkdownIt
from markdown_it.token import Token

from omf_retrieval.application.indexing.ports import (
    ParsedBlock,
    ParsedMarkdown,
    ParsedSection,
)

PARSER_VERSION = "markdown-it-py-4.2.0-omf-v1"


@dataclass(frozen=True, slots=True)
class _Heading:
    level: int
    text: str
    line_start: int
    line_after: int


@dataclass(slots=True)
class _BlockBuilder:
    kind: str
    line_start: int
    line_end: int
    raw_text: str
    level: int
    children: list["_BlockBuilder"] = field(default_factory=list)

    def freeze(self) -> ParsedBlock:
        return ParsedBlock(
            kind=self.kind,
            raw_text=self.raw_text,
            line_start=self.line_start,
            line_end=self.line_end,
            children=tuple(child.freeze() for child in self.children),
        )


_BLOCK_KIND_BY_TOKEN = {
    "paragraph_open": "paragraph",
    "blockquote_open": "blockquote",
    "bullet_list_open": "bullet_list",
    "ordered_list_open": "ordered_list",
    "list_item_open": "list_item",
    "fence": "fenced_code",
    "code_block": "indented_code",
    "table_open": "table",
    "tr_open": "table_row",
    "html_block": "html_block",
    "hr": "thematic_break",
}


class MarkdownItParser:
    """Parse CommonMark plus tables while preserving original line slices."""

    def __init__(self) -> None:
        """Create the approved Markdown parser configuration."""
        self._parser = MarkdownIt("commonmark").enable("table")

    def parse(self, source: str) -> ParsedMarkdown:
        """Return heading-delimited sections for Markdown source."""
        if type(source) is not str:
            raise ValueError("Markdown source must be an exact string")
        source_lines = source.splitlines(keepends=True)
        tokens = self._parser.parse(source)
        headings = _top_level_headings(tokens)
        sections: list[ParsedSection] = []
        section_body_starts: list[int] = []
        heading_stack: list[tuple[int, int, tuple[str, ...]]] = []

        first_heading_line = (
            headings[0].line_start if headings else len(source_lines) + 1
        )
        preamble = "".join(source_lines[: first_heading_line - 1])
        if preamble.strip():
            sections.append(
                ParsedSection(
                    ordinal=0,
                    parent_ordinal=None,
                    level=0,
                    heading=None,
                    heading_path=(),
                    body=preamble,
                    line_start=1,
                    line_end=first_heading_line - 1,
                    blocks=(),
                )
            )
            section_body_starts.append(1)

        for heading_index, heading in enumerate(headings):
            while heading_stack and heading_stack[-1][0] >= heading.level:
                heading_stack.pop()
            parent_ordinal = heading_stack[-1][1] if heading_stack else None
            parent_path = heading_stack[-1][2] if heading_stack else ()
            heading_path = (*parent_path, heading.text)
            ordinal = len(sections)
            next_line_start = (
                headings[heading_index + 1].line_start
                if heading_index + 1 < len(headings)
                else len(source_lines) + 1
            )
            sections.append(
                ParsedSection(
                    ordinal=ordinal,
                    parent_ordinal=parent_ordinal,
                    level=heading.level,
                    heading=heading.text,
                    heading_path=heading_path,
                    body="".join(
                        source_lines[heading.line_after - 1 : next_line_start - 1]
                    ),
                    line_start=heading.line_start,
                    line_end=next_line_start - 1,
                    blocks=(),
                )
            )
            section_body_starts.append(heading.line_after)
            heading_stack.append((heading.level, ordinal, heading_path))

        top_level_blocks = _mapped_blocks(tokens, source_lines)
        mapped_sections = tuple(
            replace(
                section,
                blocks=_section_blocks(
                    top_level_blocks,
                    source_lines,
                    line_start=body_start,
                    line_end=section.line_end,
                ),
            )
            for section, body_start in zip(sections, section_body_starts, strict=True)
        )
        return ParsedMarkdown(parser_version=PARSER_VERSION, sections=mapped_sections)


def _top_level_headings(tokens: list[Token]) -> tuple[_Heading, ...]:
    headings: list[_Heading] = []
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.level != 0 or token.map is None:
            continue
        inline = tokens[index + 1]
        headings.append(
            _Heading(
                level=int(token.tag[1:]),
                text=inline.content,
                line_start=token.map[0] + 1,
                line_after=token.map[1] + 1,
            )
        )
    return tuple(headings)


def _mapped_blocks(
    tokens: list[Token], source_lines: list[str]
) -> tuple[ParsedBlock, ...]:
    roots: list[_BlockBuilder] = []
    open_blocks: list[_BlockBuilder] = []
    for token in tokens:
        kind = _BLOCK_KIND_BY_TOKEN.get(token.type)
        if kind is None or token.map is None:
            continue
        while open_blocks and token.level <= open_blocks[-1].level:
            open_blocks.pop()
        builder = _BlockBuilder(
            kind=kind,
            line_start=token.map[0] + 1,
            line_end=token.map[1],
            raw_text="".join(source_lines[token.map[0] : token.map[1]]),
            level=token.level,
        )
        if open_blocks:
            open_blocks[-1].children.append(builder)
        else:
            roots.append(builder)
        if token.nesting == 1:
            open_blocks.append(builder)
    return tuple(root.freeze() for root in roots)


def _section_blocks(
    top_level_blocks: tuple[ParsedBlock, ...],
    source_lines: list[str],
    *,
    line_start: int,
    line_end: int,
) -> tuple[ParsedBlock, ...]:
    if line_start > line_end:
        return ()

    blocks: list[ParsedBlock] = []
    cursor = line_start
    for block in top_level_blocks:
        if block.line_end < line_start:
            continue
        if block.line_start > line_end:
            break
        if block.line_start < line_start or block.line_end > line_end:
            continue
        if cursor < block.line_start:
            blocks.append(_raw_gap(source_lines, cursor, block.line_start - 1))
        blocks.append(block)
        cursor = block.line_end + 1
    if cursor <= line_end:
        blocks.append(_raw_gap(source_lines, cursor, line_end))
    return tuple(blocks)


def _raw_gap(source_lines: list[str], line_start: int, line_end: int) -> ParsedBlock:
    raw_text = "".join(source_lines[line_start - 1 : line_end])
    return ParsedBlock(
        kind="blank" if not raw_text.strip() else "raw",
        raw_text=raw_text,
        line_start=line_start,
        line_end=line_end,
        children=(),
    )
