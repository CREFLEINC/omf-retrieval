"""Deterministic source-backed parent-child chunking."""

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from omf_retrieval.application.indexing.hashing import chunk_hash, config_hash
from omf_retrieval.application.indexing.ports import (
    ChunkConfig,
    ChunkDraft,
    ChunkWarning,
    MarkdownParser,
    ParentContext,
    ParsedBlock,
    ParsedMarkdown,
    ParsedSection,
    TokenCounter,
    TokenizerDescriptor,
    split_physical_lines,
)

CHUNKER_VERSION = "parent-child-v1"


@dataclass(frozen=True, slots=True)
class _Excerpt:
    raw_text: str
    line_start: int
    line_end: int
    warnings: tuple[ChunkWarning, ...] = ()
    line_numbers: tuple[int, ...] = ()
    source_start: int | None = None
    source_end: int | None = None


@dataclass(frozen=True, slots=True)
class _AtomicUnit:
    excerpt: _Excerpt
    block_kind: str
    warning_line_start: int
    warning_line_end: int


@dataclass(slots=True)
class _WindowSearchBudget:
    calls_left: int
    input_chars_left: int

    def consume(self, input_chars: int) -> None:
        if self.calls_left <= 0 or input_chars > self.input_chars_left:
            raise ValueError("Tokenizer offsets cannot form a non-empty child")
        self.calls_left -= 1
        self.input_chars_left -= input_chars


@dataclass(slots=True)
class _ContextSearchBudget:
    calls_left: int
    input_chars_left: int

    def consume(self, input_chars: int) -> bool:
        if self.calls_left <= 0 or input_chars > self.input_chars_left:
            return False
        self.calls_left -= 1
        self.input_chars_left -= input_chars
        return True


_TOKEN_COUNTER_FAILED = object()
_PARSER_FAILED = object()
_WINDOW_PROBE_LIMIT = 24
_WINDOW_SEARCH_INPUT_MULTIPLIER = 64
_TINY_CORRECTIVE_REMAINDER_TOKENS = 64
_CONTEXT_BASE_PROBES = 16
_CONTEXT_PROBES_PER_SOURCE_WINDOW = 4
_CONTEXT_SEARCH_INPUT_MULTIPLIER = 32
_CONTEXT_NEIGHBOR_RADIUS = 2


class ParentContextBuilder:
    """Build source-backed context around one matched retrieval child."""

    def __init__(
        self,
        parser: MarkdownParser,
        token_counter: TokenCounter,
        config: ChunkConfig = ChunkConfig(),  # noqa: B008
    ) -> None:
        """Bind the approved parser, tokenizer, and parent token limit."""
        parser_method = _parser_method(parser)
        token_counter_methods = _token_counter_methods(token_counter)
        if (
            parser_method is None
            or token_counter_methods is None
            or type(config) is not ChunkConfig
        ):
            raise ValueError("Invalid parent context builder contract")
        self._parse = parser_method
        self._encode, self._offsets = token_counter_methods
        self._config = config
        self._child_chunker = ParentChildChunker._from_bound_methods(
            self._encode,
            self._offsets,
            config,
        )

    def build(
        self,
        section: ParsedSection,
        *,
        matched_raw_text: str,
        matched_line_start: int,
        matched_line_end: int,
        parser_version: str,
        matched_ordinal: int | None = None,
    ) -> ParentContext:
        """Build a source-backed context around one matched child excerpt."""
        if (
            type(section) is not ParsedSection
            or type(matched_raw_text) is not str
            or not matched_raw_text
            or type(matched_line_start) is not int
            or type(matched_line_end) is not int
            or matched_line_start < 1
            or matched_line_end < matched_line_start
            or (
                matched_ordinal is not None
                and (type(matched_ordinal) is not int or matched_ordinal < 0)
            )
            or type(parser_version) is not str
            or not parser_version.strip()
        ):
            raise ValueError("Invalid parent context input contract")
        parsed = _call_parser(self._parse, section.body)
        if (
            type(parsed) is not ParsedMarkdown
            or parsed.parser_version != parser_version
            or len(parsed.sections) != 1
            or parsed.sections[0].level != 0
            or parsed.sections[0].body != section.body
            or not _valid_reparsed_root(parsed.sections[0], section.body)
        ):
            raise ValueError("Markdown parser returned invalid parent context data")

        source_lines = split_physical_lines(section.body)
        body_line_start = section.line_end - len(source_lines) + 1
        if (
            not source_lines
            or body_line_start < section.line_start
            or matched_line_start < body_line_start
            or matched_line_end > section.line_end
        ):
            raise ValueError("Invalid parent context source range")
        relative_match_start = matched_line_start - body_line_start + 1
        relative_match_end = matched_line_end - body_line_start + 1
        matched_line_text = "".join(
            source_lines[relative_match_start - 1 : relative_match_end]
        )
        if matched_raw_text not in matched_line_text:
            raise ValueError("Matched source is not present in its line range")

        matched_source_start: int | None = None
        if matched_ordinal is not None:
            drafts, excerpts = self._child_chunker._split_drafts_with_excerpts(
                section,
                parser_version=parser_version,
            )
            if matched_ordinal >= len(drafts):
                raise ValueError("Matched child ordinal is out of range")
            draft = drafts[matched_ordinal]
            located = excerpts[matched_ordinal]
            if (
                draft.ordinal != matched_ordinal
                or draft.raw_text != matched_raw_text
                or draft.line_start != matched_line_start
                or draft.line_end != matched_line_end
                or located.source_start is None
                or located.source_end is None
                or located.source_end - located.source_start != len(matched_raw_text)
                or section.body[located.source_start : located.source_end]
                != matched_raw_text
            ):
                raise ValueError("Matched child does not match reconstructed source")
            matched_source_start = located.source_start

        whole_token_count = self._token_count(section.body)
        if whole_token_count <= self._config.parent_context_max_tokens:
            return ParentContext(
                raw_text=section.body,
                token_count=whole_token_count,
                line_start=body_line_start,
                line_end=section.line_end,
            )

        excerpt = self._select_blocks(
            parsed.sections[0].blocks,
            source_lines,
            body_line_start=body_line_start,
            matched_line_start=relative_match_start,
            matched_line_end=relative_match_end,
            matched_raw_text=matched_raw_text,
            matched_source_start=matched_source_start,
        )
        token_count = self._token_count(excerpt.raw_text)
        return ParentContext(
            raw_text=excerpt.raw_text,
            token_count=token_count,
            line_start=excerpt.line_start,
            line_end=excerpt.line_end,
        )

    def _select_blocks(
        self,
        blocks: tuple[ParsedBlock, ...],
        source_lines: tuple[str, ...],
        *,
        body_line_start: int,
        matched_line_start: int,
        matched_line_end: int,
        matched_raw_text: str,
        matched_source_start: int | None,
    ) -> _Excerpt:
        seed_indices = tuple(
            index
            for index, block in enumerate(blocks)
            if block.line_start <= matched_line_end
            and block.line_end >= matched_line_start
        )
        if not seed_indices:
            raise ValueError("Matched source has no parser block")
        seed_left = seed_indices[0]
        seed_right = seed_indices[-1]
        left = seed_left
        right = seed_right
        excerpt = _relative_line_excerpt(
            source_lines,
            body_line_start=body_line_start,
            line_start=blocks[left].line_start,
            line_end=blocks[right].line_end,
        )
        seed_token_count = self._token_count(excerpt.raw_text)
        if seed_token_count > self._config.parent_context_max_tokens:
            if seed_left != seed_right:
                raise ValueError(
                    "Matched parser blocks exceed the parent context limit"
                )
            matched_lines_start = sum(
                len(line) for line in source_lines[: matched_line_start - 1]
            )
            matched_lines_text = "".join(
                source_lines[matched_line_start - 1 : matched_line_end]
            )
            block_start = sum(
                len(line) for line in source_lines[: blocks[left].line_start - 1]
            )
            if matched_source_start is None:
                occurrence = _unique_occurrence(matched_lines_text, matched_raw_text)
                if occurrence is None:
                    raise ValueError("Matched source is ambiguous in its line range")
                matched_start = matched_lines_start + occurrence - block_start
            else:
                matched_start = matched_source_start - block_start
                if (
                    matched_start < 0
                    or matched_start + len(matched_raw_text) > len(excerpt.raw_text)
                    or excerpt.raw_text[
                        matched_start : matched_start + len(matched_raw_text)
                    ]
                    != matched_raw_text
                ):
                    raise ValueError("Matched child is outside its parser block")
            return self._oversized_block_excerpt(
                excerpt,
                matched_start=matched_start,
                matched_end=matched_start + len(matched_raw_text),
            )

        expansion_windows = _block_expansion_windows(
            len(blocks), seed_left=seed_left, seed_right=seed_right
        )
        source_length = sum(len(line) for line in source_lines)
        search_budget = _context_search_budget(
            source_length,
            self._config.parent_context_max_tokens,
        )
        if not _can_exhaustively_expand_blocks(
            expansion_windows,
            source_length=source_length,
            search_budget=search_budget,
        ):
            return self._select_blocks_bounded(
                blocks,
                source_lines,
                body_line_start=body_line_start,
                expansion_windows=expansion_windows,
                seed_excerpt=excerpt,
                search_budget=search_budget,
            )

        while left > 0 or right + 1 < len(blocks):
            candidates: list[tuple[int, int, str]] = []
            if left > 0:
                candidates.append((seed_left - left + 1, 0, "left"))
            if right + 1 < len(blocks):
                candidates.append((right - seed_right + 1, 1, "right"))
            added = False
            for _, _, side in sorted(candidates):
                candidate_left = left - 1 if side == "left" else left
                candidate_right = right + 1 if side == "right" else right
                candidate = _relative_line_excerpt(
                    source_lines,
                    body_line_start=body_line_start,
                    line_start=blocks[candidate_left].line_start,
                    line_end=blocks[candidate_right].line_end,
                )
                if self._token_count(candidate.raw_text) <= (
                    self._config.parent_context_max_tokens
                ):
                    left = candidate_left
                    right = candidate_right
                    excerpt = candidate
                    added = True
                    break
            if not added:
                break
        return excerpt

    def _select_blocks_bounded(
        self,
        blocks: tuple[ParsedBlock, ...],
        source_lines: tuple[str, ...],
        *,
        body_line_start: int,
        expansion_windows: tuple[tuple[int, int], ...],
        seed_excerpt: _Excerpt,
        search_budget: _ContextSearchBudget,
    ) -> _Excerpt:
        limit = self._config.parent_context_max_tokens
        source_token_counts = _block_window_source_token_counts(
            source_lines,
            blocks,
            expansion_windows,
            self._token_offsets("".join(source_lines)),
        )
        candidate_indices = _block_context_probe_indices(
            source_token_counts,
            token_limit=limit,
        )
        best = seed_excerpt
        seed_left, seed_right = expansion_windows[0]
        best_left = seed_left
        best_right = seed_right
        for index in candidate_indices:
            left, right = expansion_windows[index]
            candidate = _relative_line_excerpt(
                source_lines,
                body_line_start=body_line_start,
                line_start=blocks[left].line_start,
                line_end=blocks[right].line_end,
            )
            if not search_budget.consume(len(candidate.raw_text)):
                break
            if self._token_count(candidate.raw_text) > limit:
                continue
            if len(candidate.raw_text) > len(best.raw_text):
                best = candidate
                best_left = left
                best_right = right
        while best_left > 0 or best_right + 1 < len(blocks):
            candidates: list[tuple[int, int, str]] = []
            if best_left > 0:
                candidates.append((seed_left - best_left + 1, 0, "left"))
            if best_right + 1 < len(blocks):
                candidates.append((best_right - seed_right + 1, 1, "right"))
            added = False
            for _, _, side in sorted(candidates):
                candidate_left = best_left - 1 if side == "left" else best_left
                candidate_right = best_right + 1 if side == "right" else best_right
                candidate = _relative_line_excerpt(
                    source_lines,
                    body_line_start=body_line_start,
                    line_start=blocks[candidate_left].line_start,
                    line_end=blocks[candidate_right].line_end,
                )
                if not search_budget.consume(len(candidate.raw_text)):
                    return best
                if self._token_count(candidate.raw_text) <= limit:
                    best = candidate
                    best_left = candidate_left
                    best_right = candidate_right
                    added = True
                    break
            if not added:
                break
        return best

    def _oversized_block_excerpt(
        self,
        excerpt: _Excerpt,
        *,
        matched_start: int,
        matched_end: int,
    ) -> _Excerpt:
        limit = self._config.parent_context_max_tokens
        matched_token_count = self._token_count(
            excerpt.raw_text[matched_start:matched_end]
        )
        if matched_token_count > limit:
            raise ValueError("Matched source exceeds the parent context limit")
        offsets = self._token_offsets(excerpt.raw_text)
        windows = _context_expansion_windows(
            offsets,
            matched_start=matched_start,
            matched_end=matched_end,
        )
        candidate_indices = _context_probe_indices(
            len(windows),
            expected_expansions=max(0, limit - matched_token_count),
        )
        budget = _context_search_budget(len(excerpt.raw_text), limit)
        best_start = matched_start
        best_end = matched_end
        for index in candidate_indices:
            candidate_start, candidate_end = windows[index]
            candidate_text = excerpt.raw_text[candidate_start:candidate_end]
            if not budget.consume(len(candidate_text)):
                break
            if self._token_count(candidate_text) > limit:
                continue
            if (candidate_end - candidate_start) > (best_end - best_start) or (
                candidate_end - candidate_start == best_end - best_start
                and candidate_start < best_start
            ):
                best_start = candidate_start
                best_end = candidate_end
        return _slice_excerpt(excerpt, best_start, best_end)

    def _token_count(self, text: str) -> int:
        return len(self._encoded_tokens(text))

    def _token_offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        tokens = self._encoded_tokens(text)
        result = _call_token_counter(self._offsets, text)
        if result is _TOKEN_COUNTER_FAILED:
            raise ValueError("Token counter failed") from None
        materialized = _materialize_sequence(result, max_items=len(text))
        if materialized is _TOKEN_COUNTER_FAILED:
            raise ValueError("Token counter failed") from None
        if materialized is None:
            raise ValueError("Token counter returned malformed data") from None
        declared_length, spans = materialized
        if declared_length != len(tokens):
            raise ValueError("Token counter returned malformed data") from None

        offsets: list[tuple[int, int]] = []
        previous_end = 0
        for span in spans:
            if (
                type(span) is not tuple
                or len(span) != 2
                or type(span[0]) is not int
                or type(span[1]) is not int
            ):
                raise ValueError("Token counter returned malformed data") from None
            start, end = span
            if start < previous_end or start < 0 or end <= start or end > len(text):
                raise ValueError("Token counter returned malformed data") from None
            offsets.append((start, end))
            previous_end = end
        return tuple(offsets)

    def _encoded_tokens(self, text: str) -> tuple[int, ...]:
        result = _call_token_counter(self._encode, text)
        if result is _TOKEN_COUNTER_FAILED:
            raise ValueError("Token counter failed") from None
        materialized = _materialize_sequence(result, max_items=len(text))
        if materialized is _TOKEN_COUNTER_FAILED:
            raise ValueError("Token counter failed") from None
        if materialized is None:
            raise ValueError("Token counter returned malformed data") from None
        declared_length, tokens = materialized
        if any(type(token) is not int for token in tokens) or (
            text and declared_length == 0
        ):
            raise ValueError("Token counter returned malformed data") from None
        return tokens


class ParentChildChunker:
    """Split parsed Markdown sections into deterministic retrieval children."""

    def __init__(
        self,
        token_counter: TokenCounter,
        tokenizer_descriptor: TokenizerDescriptor,
        config: ChunkConfig = ChunkConfig(),  # noqa: B008
    ) -> None:
        """Bind source tokenization behavior and deterministic chunk settings."""
        if (
            type(tokenizer_descriptor) is not TokenizerDescriptor
            or type(config) is not ChunkConfig
        ):
            raise ValueError("Invalid chunker input contract")
        token_counter_methods = _token_counter_methods(token_counter)
        if token_counter_methods is None:
            raise ValueError("Invalid chunker input contract")
        self._encode, self._offsets = token_counter_methods
        self._config = config
        self._config_hash = chunk_config_identity_hash(config, tokenizer_descriptor)

    @classmethod
    def _from_bound_methods(
        cls,
        encode: Callable[[str], object],
        offsets: Callable[[str], object],
        config: ChunkConfig,
    ) -> "ParentChildChunker":
        instance = cls.__new__(cls)
        instance._encode = encode
        instance._offsets = offsets
        instance._config = config
        instance._config_hash = "0" * 64
        return instance

    def split(
        self, section: ParsedSection, *, parser_version: str
    ) -> tuple[ChunkDraft, ...]:
        """Create source-backed children for one parsed Markdown section."""
        drafts, _ = self._split_drafts_with_excerpts(
            section,
            parser_version=parser_version,
        )
        return drafts

    def _split_drafts_with_excerpts(
        self,
        section: ParsedSection,
        *,
        parser_version: str,
    ) -> tuple[tuple[ChunkDraft, ...], tuple[_Excerpt, ...]]:
        if (
            type(section) is not ParsedSection
            or type(parser_version) is not str
            or not parser_version.strip()
        ):
            raise ValueError("Invalid split input contract")
        if not section.body.strip():
            return (), ()

        heading_prefix = (
            "\n".join(section.heading_path) + "\n" if section.heading_path else ""
        )
        whole_section = _Excerpt(
            raw_text=section.body,
            line_start=min(block.line_start for block in section.blocks),
            line_end=max(block.line_end for block in section.blocks),
            source_start=0,
            source_end=len(section.body),
        )
        if self._token_count(heading_prefix + section.body) <= (
            self._config.soft_max_tokens
        ):
            excerpts = (whole_section,)
        else:
            excerpts = self._split_blocks(section.blocks, heading_prefix)
        drafts = tuple(
            self._draft(
                excerpt,
                ordinal=ordinal,
                heading_prefix=heading_prefix,
                heading_path=section.heading_path,
                parser_version=parser_version,
            )
            for ordinal, excerpt in enumerate(excerpts)
        )
        return drafts, excerpts

    def _split_blocks(
        self, blocks: tuple[ParsedBlock, ...], heading_prefix: str
    ) -> tuple[_Excerpt, ...]:
        window_limit: int | None = None
        chunks: list[_Excerpt] = []
        normal_blocks: list[_Excerpt] = []
        separator_blocks: list[_Excerpt] = []
        source_cursor = 0
        for block in blocks:
            block_excerpt = _Excerpt(
                raw_text=block.raw_text,
                line_start=block.line_start,
                line_end=block.line_end,
                source_start=source_cursor,
                source_end=source_cursor + len(block.raw_text),
            )
            source_cursor += len(block.raw_text)
            if not block.raw_text.strip():
                if normal_blocks:
                    separator_blocks.append(block_excerpt)
                continue
            if block.kind not in {"table", "bullet_list", "ordered_list", "blockquote"}:
                if separator_blocks:
                    candidate_blocks = (
                        *normal_blocks,
                        *separator_blocks,
                        block_excerpt,
                    )
                    candidate_raw = "".join(
                        candidate.raw_text for candidate in candidate_blocks
                    )
                    if window_limit is None:
                        prefix_tokens = self._token_count(heading_prefix)
                        target_budget = self._config.target_tokens - prefix_tokens
                        use_target_window = target_budget >= max(
                            1, 2 * self._config.overlap_tokens
                        )
                        window_limit = (
                            self._config.target_tokens
                            if use_target_window
                            else self._config.soft_max_tokens
                        )
                    if self._token_count(heading_prefix + candidate_raw) > window_limit:
                        chunks.extend(
                            self._split_normal_blocks(
                                tuple(normal_blocks), heading_prefix
                            )
                        )
                        normal_blocks.clear()
                    else:
                        normal_blocks.extend(separator_blocks)
                separator_blocks.clear()
                normal_blocks.append(block_excerpt)
                continue
            separator_blocks.clear()
            if normal_blocks:
                chunks.extend(
                    self._split_normal_blocks(tuple(normal_blocks), heading_prefix)
                )
                normal_blocks.clear()
            chunks.extend(
                self._split_atomic_block(
                    block,
                    heading_prefix,
                    source_start=block_excerpt.source_start,
                )
            )
        if normal_blocks:
            chunks.extend(
                self._split_normal_blocks(tuple(normal_blocks), heading_prefix)
            )
        return tuple(chunks)

    def _split_atomic_block(
        self,
        block: ParsedBlock,
        heading_prefix: str,
        *,
        source_start: int | None,
    ) -> tuple[_Excerpt, ...]:
        whole_block = _trim_excerpt_whitespace_lines(
            _Excerpt(
                block.raw_text,
                block.line_start,
                block.line_end,
                source_start=source_start,
                source_end=(
                    source_start + len(block.raw_text)
                    if source_start is not None
                    else None
                ),
            )
        )
        if not whole_block.raw_text:
            return ()
        if self._token_count(heading_prefix + whole_block.raw_text) <= (
            self._config.atomic_max_tokens
        ):
            return (whole_block,)

        chunks: list[_Excerpt] = []
        pending: _Excerpt | None = None
        for unit in _trim_atomic_boundary_units(
            _atomic_units(block, source_start=source_start)
        ):
            if self._token_count(heading_prefix + unit.excerpt.raw_text) > (
                self._config.atomic_max_tokens
            ):
                if pending is not None:
                    chunks.append(pending)
                    pending = None
                chunks.extend(self._split_oversized_atomic_unit(unit, heading_prefix))
                continue
            if pending is None:
                pending = unit.excerpt
                continue
            candidate = _join_excerpts(pending, unit.excerpt)
            if self._token_count(heading_prefix + candidate.raw_text) <= (
                self._config.atomic_max_tokens
            ):
                pending = candidate
            else:
                chunks.append(pending)
                pending = unit.excerpt
        if pending is not None:
            chunks.append(pending)
        return tuple(chunks)

    def _split_oversized_atomic_unit(
        self, unit: _AtomicUnit, heading_prefix: str
    ) -> tuple[_Excerpt, ...]:
        warning = ChunkWarning(
            block_kind=unit.block_kind,
            line_start=unit.warning_line_start,
            line_end=unit.warning_line_end,
        )
        offsets = self._token_offsets(unit.excerpt.raw_text)
        line_ends = _physical_line_ends(unit.excerpt.raw_text)
        prefix_tokens = self._token_count(heading_prefix)
        source_budget = self._config.atomic_max_tokens - prefix_tokens
        if source_budget <= 0:
            raise ValueError("Heading path leaves no room for child source text")
        search_budget = _window_search_budget(
            unit.excerpt.raw_text,
            heading_prefix,
            source_tokens=len(offsets),
            source_budget=source_budget,
            overlap_tokens=self._config.overlap_tokens,
        )

        chunks: list[_Excerpt] = []
        cursor = 0
        while cursor < len(offsets):
            window_end = self._bounded_window_end(
                unit.excerpt.raw_text,
                offsets,
                start=cursor,
                source_budget=source_budget,
                heading_prefix=heading_prefix,
                token_limit=self._config.atomic_max_tokens,
                search_budget=search_budget,
                overlap_tokens=0,
                allow_reduced_progress=True,
            )
            if window_end is None:
                raise ValueError("Tokenizer offsets cannot form a non-empty child")
            piece = _slice_excerpt_with_line_ends(
                unit.excerpt,
                _token_boundary(offsets, cursor, len(unit.excerpt.raw_text)),
                _token_boundary(offsets, window_end, len(unit.excerpt.raw_text)),
                line_ends,
            )
            chunks.append(_with_warning(piece, warning))
            cursor = window_end
        return tuple(chunks)

    def _split_normal_blocks(
        self, blocks: tuple[_Excerpt, ...], heading_prefix: str
    ) -> tuple[_Excerpt, ...]:
        if len(blocks) == 1:
            chunks, pending = self._split_oversized_normal(blocks[0], heading_prefix)
            return (*chunks, pending)

        prefix_tokens = self._token_count(heading_prefix)
        target_budget = self._config.target_tokens - prefix_tokens
        use_target_window = target_budget >= max(1, 2 * self._config.overlap_tokens)
        window_limit = (
            self._config.target_tokens
            if use_target_window
            else self._config.soft_max_tokens
        )
        chunks: list[_Excerpt] = []
        pending: _Excerpt | None = None
        for block_excerpt in blocks:
            if pending is None:
                pending = block_excerpt
            else:
                candidate = _join_excerpts(pending, block_excerpt)
                candidate_tokens = self._token_count(
                    heading_prefix + candidate.raw_text
                )
                if candidate_tokens <= window_limit:
                    pending = candidate
                else:
                    chunks.append(pending)
                    pending = _join_excerpts(
                        self._overlap_suffix(pending), block_excerpt
                    )
            if (
                self._token_count(heading_prefix + pending.raw_text)
                > self._config.soft_max_tokens
            ):
                oversized_chunks, pending = self._split_oversized_normal(
                    pending, heading_prefix
                )
                chunks.extend(oversized_chunks)

        if pending is not None:
            chunks.append(pending)
        return tuple(chunks)

    def _split_oversized_normal(
        self, excerpt: _Excerpt, heading_prefix: str
    ) -> tuple[tuple[_Excerpt, ...], _Excerpt]:
        offsets = self._token_offsets(excerpt.raw_text)
        line_ends = _physical_line_ends(excerpt.raw_text)
        prefix_tokens = self._token_count(heading_prefix)
        soft_budget = self._config.soft_max_tokens - prefix_tokens
        if soft_budget <= 0:
            raise ValueError("Heading path leaves no room for child source text")
        target_budget = self._config.target_tokens - prefix_tokens
        use_target_window = target_budget >= max(1, 2 * self._config.overlap_tokens)
        window_budget = target_budget if use_target_window else soft_budget
        window_limit = (
            self._config.target_tokens
            if use_target_window
            else self._config.soft_max_tokens
        )
        search_budget = _window_search_budget(
            excerpt.raw_text,
            heading_prefix,
            source_tokens=len(offsets),
            source_budget=window_budget,
            overlap_tokens=self._config.overlap_tokens,
        )

        chunks: list[_Excerpt] = []
        cursor = 0
        covered_end = 0
        while True:
            remaining_tokens = len(offsets) - cursor
            if remaining_tokens <= soft_budget:
                pending = _slice_excerpt_with_line_ends(
                    excerpt,
                    _token_boundary(offsets, cursor, len(excerpt.raw_text)),
                    len(excerpt.raw_text),
                    line_ends,
                )
                if (
                    self._token_count(heading_prefix + pending.raw_text)
                    <= self._config.soft_max_tokens
                ):
                    return tuple(chunks), pending
                source_budget = soft_budget
                token_limit = self._config.soft_max_tokens
            else:
                source_budget = window_budget
                token_limit = window_limit

            window_end = self._bounded_window_end(
                excerpt.raw_text,
                offsets,
                start=cursor,
                source_budget=source_budget,
                heading_prefix=heading_prefix,
                token_limit=token_limit,
                search_budget=search_budget,
                overlap_tokens=self._config.overlap_tokens,
                allow_reduced_progress=token_limit == self._config.soft_max_tokens,
            )
            if window_end is None and token_limit < self._config.soft_max_tokens:
                window_end = self._bounded_window_end(
                    excerpt.raw_text,
                    offsets,
                    start=cursor,
                    source_budget=soft_budget,
                    heading_prefix=heading_prefix,
                    token_limit=self._config.soft_max_tokens,
                    search_budget=search_budget,
                    overlap_tokens=self._config.overlap_tokens,
                    allow_reduced_progress=True,
                )
            if window_end is None:
                raise ValueError("Tokenizer offsets cannot form a non-empty child")
            if window_end <= covered_end:
                cursor = covered_end
                continue
            piece = _slice_excerpt_with_line_ends(
                excerpt,
                _token_boundary(offsets, cursor, len(excerpt.raw_text)),
                _token_boundary(offsets, window_end, len(excerpt.raw_text)),
                line_ends,
            )
            chunks.append(piece)
            piece_tokens = window_end - cursor
            effective_overlap = min(self._config.overlap_tokens, piece_tokens // 2)
            covered_end = window_end
            cursor = window_end - effective_overlap

    def _bounded_window_end(
        self,
        raw_text: str,
        offsets: tuple[tuple[int, int], ...],
        *,
        start: int,
        source_budget: int,
        heading_prefix: str,
        token_limit: int,
        search_budget: _WindowSearchBudget,
        overlap_tokens: int,
        allow_reduced_progress: bool,
    ) -> int | None:
        candidate = min(start + source_budget, len(offsets))
        start_offset = _token_boundary(offsets, start, len(raw_text))
        available_tokens = candidate - start
        if available_tokens <= 0:
            return None

        probes = 0

        def token_count(end: int) -> int:
            nonlocal probes
            probes += 1
            if probes > _WINDOW_PROBE_LIMIT:
                raise ValueError("Tokenizer offsets cannot form a non-empty child")
            end_offset = _token_boundary(offsets, end, len(raw_text))
            probe_text = heading_prefix + raw_text[start_offset:end_offset]
            search_budget.consume(len(probe_text))
            return self._token_count(probe_text)

        candidate_count = token_count(candidate)
        if candidate_count <= token_limit:
            return candidate

        estimated_capacity = max(
            1,
            available_tokens - max(0, candidate_count - token_limit),
        )
        remaining_tokens = len(offsets) - start
        configured_progress = _configured_window_progress(
            source_budget, overlap_tokens=overlap_tokens
        )
        if (
            estimated_capacity < configured_progress
            and remaining_tokens > _TINY_CORRECTIVE_REMAINDER_TOKENS
            and (
                not allow_reduced_progress
                or not _bounded_corrective_amplification(
                    remaining_tokens,
                    source_budget=source_budget,
                    piece_tokens=estimated_capacity,
                    overlap_tokens=overlap_tokens,
                )
            )
        ):
            return None
        minimum_progress = min(
            available_tokens,
            estimated_capacity,
            configured_progress,
        )
        minimum_end = start + minimum_progress
        estimated_end = start + estimated_capacity
        candidates = _window_probe_ends(
            minimum_end=minimum_end,
            estimated_end=estimated_end,
            maximum_end=candidate,
        )

        best: int | None = None
        for end in candidates:
            if token_count(end) <= token_limit and (best is None or end > best):
                best = end
        return best

    def _overlap_suffix(self, excerpt: _Excerpt) -> _Excerpt:
        offsets = self._token_offsets(excerpt.raw_text)
        overlap_count = min(self._config.overlap_tokens, len(offsets) // 2)
        if overlap_count == 0:
            return _slice_excerpt(excerpt, len(excerpt.raw_text), len(excerpt.raw_text))
        start = offsets[-overlap_count][0]
        return _slice_excerpt(excerpt, start, len(excerpt.raw_text))

    def _draft(
        self,
        excerpt: _Excerpt,
        *,
        ordinal: int,
        heading_prefix: str,
        heading_path: tuple[str, ...],
        parser_version: str,
    ) -> ChunkDraft:
        search_text = heading_prefix + excerpt.raw_text
        token_count = self._token_count(search_text)
        return ChunkDraft(
            ordinal=ordinal,
            raw_text=excerpt.raw_text,
            search_text=search_text,
            token_count=token_count,
            line_start=excerpt.line_start,
            line_end=excerpt.line_end,
            chunk_hash=chunk_hash(
                parser_version=parser_version,
                chunk_config_hash=self._config_hash,
                heading_path=heading_path,
                line_start=excerpt.line_start,
                line_end=excerpt.line_end,
                raw_text=excerpt.raw_text,
                search_text=search_text,
            ),
            warnings=excerpt.warnings,
        )

    def _token_count(self, text: str) -> int:
        return len(self._encoded_tokens(text))

    def _token_offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        tokens = self._encoded_tokens(text)
        result = _call_token_counter(self._offsets, text)
        if result is _TOKEN_COUNTER_FAILED:
            raise ValueError("Token counter failed") from None
        materialized = _materialize_sequence(result, max_items=len(text))
        if materialized is _TOKEN_COUNTER_FAILED:
            raise ValueError("Token counter failed") from None
        if materialized is None:
            raise ValueError("Token counter returned malformed data") from None
        declared_length, spans = materialized
        if declared_length != len(tokens):
            raise ValueError("Token counter returned malformed data") from None

        offsets: list[tuple[int, int]] = []
        previous_end = 0
        for span in spans:
            if (
                type(span) is not tuple
                or len(span) != 2
                or type(span[0]) is not int
                or type(span[1]) is not int
            ):
                raise ValueError("Token counter returned malformed data") from None
            start, end = span
            if start < previous_end or start < 0 or end <= start or end > len(text):
                raise ValueError("Token counter returned malformed data") from None
            offsets.append((start, end))
            previous_end = end
        return tuple(offsets)

    def _encoded_tokens(self, text: str) -> tuple[int, ...]:
        result = _call_token_counter(self._encode, text)
        if result is _TOKEN_COUNTER_FAILED:
            raise ValueError("Token counter failed") from None
        materialized = _materialize_sequence(result, max_items=len(text))
        if materialized is _TOKEN_COUNTER_FAILED:
            raise ValueError("Token counter failed") from None
        if materialized is None:
            raise ValueError("Token counter returned malformed data") from None
        declared_length, tokens = materialized
        if any(type(token) is not int for token in tokens) or (
            text and declared_length == 0
        ):
            raise ValueError("Token counter returned malformed data") from None
        return tokens


def _configured_window_progress(source_budget: int, *, overlap_tokens: int) -> int:
    configured_progress = (
        2 * overlap_tokens if overlap_tokens else (source_budget + 1) // 2
    )
    return min(source_budget, max(1, configured_progress))


def _bounded_corrective_amplification(
    remaining_tokens: int,
    *,
    source_budget: int,
    piece_tokens: int,
    overlap_tokens: int,
) -> bool:
    configured_overlap = min(overlap_tokens, source_budget // 2)
    configured_advance = max(1, source_budget - configured_overlap)
    piece_overlap = min(overlap_tokens, piece_tokens // 2)
    piece_advance = max(1, piece_tokens - piece_overlap)
    configured_windows = (
        remaining_tokens + configured_advance - 1
    ) // configured_advance
    corrective_windows = (remaining_tokens + piece_advance - 1) // piece_advance
    return corrective_windows <= _WINDOW_PROBE_LIMIT * configured_windows


def _window_search_budget(
    raw_text: str,
    heading_prefix: str,
    *,
    source_tokens: int,
    source_budget: int,
    overlap_tokens: int,
) -> _WindowSearchBudget:
    minimum_progress = _configured_window_progress(
        source_budget, overlap_tokens=overlap_tokens
    )
    minimum_overlap = min(overlap_tokens, minimum_progress // 2)
    minimum_advance = max(1, minimum_progress - minimum_overlap)
    expected_windows = (source_tokens + minimum_advance - 1) // minimum_advance + 1
    return _WindowSearchBudget(
        calls_left=2 * _WINDOW_PROBE_LIMIT * expected_windows,
        input_chars_left=_WINDOW_SEARCH_INPUT_MULTIPLIER
        * max(1, len(raw_text) + expected_windows * len(heading_prefix)),
    )


def _window_probe_ends(
    *, minimum_end: int, estimated_end: int, maximum_end: int
) -> tuple[int, ...]:
    centers = (
        estimated_end,
        minimum_end,
        maximum_end,
        (minimum_end + maximum_end) // 2,
        minimum_end + (maximum_end - minimum_end) // 4,
        minimum_end + 3 * (maximum_end - minimum_end) // 4,
    )
    candidates: list[int] = []
    seen: set[int] = set()

    def add(candidate: int) -> None:
        if (
            minimum_end <= candidate <= maximum_end
            and candidate not in seen
            and len(candidates) < _WINDOW_PROBE_LIMIT - 1
        ):
            seen.add(candidate)
            candidates.append(candidate)

    for center in centers:
        add(center)
    distance = 1
    while len(candidates) < _WINDOW_PROBE_LIMIT - 1 and distance <= maximum_end:
        before = len(candidates)
        add(estimated_end - distance)
        add(estimated_end + distance)
        if len(candidates) == before and (
            estimated_end - distance < minimum_end
            and estimated_end + distance > maximum_end
        ):
            break
        distance *= 2
    return tuple(candidates)


def _block_expansion_windows(
    block_count: int, *, seed_left: int, seed_right: int
) -> tuple[tuple[int, int], ...]:
    left = seed_left
    right = seed_right
    windows = [(left, right)]
    while left > 0 or right + 1 < block_count:
        candidates: list[tuple[int, int, str]] = []
        if left > 0:
            candidates.append((seed_left - left + 1, 0, "left"))
        if right + 1 < block_count:
            candidates.append((right - seed_right + 1, 1, "right"))
        _, _, side = min(candidates)
        if side == "left":
            left -= 1
        else:
            right += 1
        windows.append((left, right))
    return tuple(windows)


def _block_window_source_token_counts(
    source_lines: tuple[str, ...],
    blocks: tuple[ParsedBlock, ...],
    expansion_windows: tuple[tuple[int, int], ...],
    source_offsets: tuple[tuple[int, int], ...],
) -> tuple[int, ...]:
    line_ends: list[int] = []
    cursor = 0
    for line in source_lines:
        cursor += len(line)
        line_ends.append(cursor)
    token_starts = tuple(start for start, _ in source_offsets)
    token_ends = tuple(end for _, end in source_offsets)
    counts: list[int] = []
    for left, right in expansion_windows:
        line_start = blocks[left].line_start
        line_end = blocks[right].line_end
        char_start = line_ends[line_start - 2] if line_start > 1 else 0
        char_end = line_ends[line_end - 1]
        first_token = bisect_right(token_ends, char_start)
        past_last_token = bisect_left(token_starts, char_end)
        counts.append(max(0, past_last_token - first_token))
    return tuple(counts)


def _block_context_probe_indices(
    source_token_counts: tuple[int, ...], *, token_limit: int
) -> tuple[int, ...]:
    last = len(source_token_counts) - 1
    estimated = max(0, bisect_right(source_token_counts, token_limit) - 1)
    indices: list[int] = []
    seen: set[int] = set()

    def add(candidate: int) -> None:
        if (
            0 <= candidate <= last
            and candidate not in seen
            and len(indices) < _CONTEXT_BASE_PROBES
        ):
            seen.add(candidate)
            indices.append(candidate)

    add(estimated)
    distance = 1
    while len(indices) < _CONTEXT_BASE_PROBES - 3 and distance <= last:
        add(estimated - distance)
        add(estimated + distance)
        distance *= 2
    add(last)
    add(last - 1)
    add(0)
    return tuple(indices)


def _can_exhaustively_expand_blocks(
    expansion_windows: tuple[tuple[int, int], ...],
    *,
    source_length: int,
    search_budget: _ContextSearchBudget,
) -> bool:
    candidate_count = max(0, len(expansion_windows) - 1)
    return (
        2 * candidate_count <= search_budget.calls_left
        and 2 * candidate_count * source_length <= search_budget.input_chars_left
    )


def _context_expansion_windows(
    offsets: tuple[tuple[int, int], ...],
    *,
    matched_start: int,
    matched_end: int,
) -> tuple[tuple[int, int], ...]:
    left_candidates = sorted(
        {start for start, _ in offsets if start < matched_start}, reverse=True
    )
    right_candidates = sorted({end for _, end in offsets if end > matched_end})
    start = matched_start
    end = matched_end
    left_index = 0
    right_index = 0
    windows = [(start, end)]
    while left_index < len(left_candidates) or right_index < len(right_candidates):
        sides: list[tuple[int, int, str]] = []
        if left_index < len(left_candidates):
            sides.append((matched_start - left_candidates[left_index], 0, "left"))
        if right_index < len(right_candidates):
            sides.append((right_candidates[right_index] - matched_end, 1, "right"))
        _, _, side = min(sides)
        if side == "left":
            start = left_candidates[left_index]
            left_index += 1
        else:
            end = right_candidates[right_index]
            right_index += 1
        windows.append((start, end))
    return tuple(windows)


def _context_probe_indices(
    window_count: int, *, expected_expansions: int
) -> tuple[int, ...]:
    last = window_count - 1
    expected = min(expected_expansions, last)
    centers = (last, expected, last // 2, last // 4, 3 * last // 4)
    indices: list[int] = []
    seen: set[int] = set()
    for center_index, center in enumerate(centers):
        candidates = [center]
        if center_index < 2:
            for distance in range(1, _CONTEXT_NEIGHBOR_RADIUS + 1):
                candidates.extend((center - distance, center + distance))
        for candidate in candidates:
            if 0 <= candidate <= last and candidate not in seen:
                seen.add(candidate)
                indices.append(candidate)
    return tuple(indices)


def _context_search_budget(source_length: int, limit: int) -> _ContextSearchBudget:
    source_windows = max(1, (source_length + limit - 1) // limit)
    return _ContextSearchBudget(
        calls_left=(
            _CONTEXT_BASE_PROBES + _CONTEXT_PROBES_PER_SOURCE_WINDOW * source_windows
        ),
        input_chars_left=_CONTEXT_SEARCH_INPUT_MULTIPLIER * (source_length + limit),
    )


def _token_counter_methods(
    token_counter: object,
) -> tuple[Callable[[str], object], Callable[[str], object]] | None:
    try:
        encode = getattr(token_counter, "encode", None)
        offsets = getattr(token_counter, "offsets", None)
    except Exception:
        return None
    if not callable(encode) or not callable(offsets):
        return None
    return encode, offsets


def _parser_method(parser: object) -> Callable[[str], object] | None:
    try:
        parse = getattr(parser, "parse", None)
    except Exception:
        return None
    return parse if callable(parse) else None


def _call_parser(operation: Callable[[str], object], text: str) -> object:
    try:
        return operation(text)
    except Exception:
        return _PARSER_FAILED


def _call_token_counter(operation: Callable[[str], object], text: str) -> object:
    try:
        return operation(text)
    except Exception:
        return _TOKEN_COUNTER_FAILED


def _materialize_sequence(result: object, *, max_items: int) -> object:
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes, bytearray)):
        return None
    try:
        declared_length = len(result)
        if declared_length > max_items:
            return None
        iterator = iter(result)
        items: list[object] = []
        for _ in range(declared_length):
            try:
                items.append(next(iterator))
            except StopIteration:
                return None
        try:
            next(iterator)
        except StopIteration:
            return declared_length, tuple(items)
    except Exception:
        return _TOKEN_COUNTER_FAILED
    return None


def _join_excerpts(first: _Excerpt, second: _Excerpt) -> _Excerpt:
    if not first.raw_text:
        return second
    if not second.raw_text:
        return first
    return _Excerpt(
        raw_text=first.raw_text + second.raw_text,
        line_start=min(first.line_start, second.line_start),
        line_end=max(first.line_end, second.line_end),
        warnings=first.warnings + second.warnings,
        line_numbers=(*_excerpt_line_numbers(first), *_excerpt_line_numbers(second)),
        source_start=first.source_start,
        source_end=second.source_end,
    )


def _excerpt_line_numbers(excerpt: _Excerpt) -> tuple[int, ...]:
    if excerpt.line_numbers:
        return excerpt.line_numbers
    return tuple(range(excerpt.line_start, excerpt.line_end + 1))


def _relative_line_excerpt(
    source_lines: tuple[str, ...],
    *,
    body_line_start: int,
    line_start: int,
    line_end: int,
) -> _Excerpt:
    return _Excerpt(
        raw_text="".join(source_lines[line_start - 1 : line_end]),
        line_start=body_line_start + line_start - 1,
        line_end=body_line_start + line_end - 1,
    )


def _unique_occurrence(source: str, target: str) -> int | None:
    first = source.find(target)
    if first < 0 or source.find(target, first + 1) >= 0:
        return None
    return first


def _valid_reparsed_root(section: ParsedSection, source: str) -> bool:
    source_lines = split_physical_lines(source)
    if (
        section.ordinal != 0
        or section.parent_ordinal is not None
        or section.heading is not None
        or section.heading_path
        or section.line_start != 1
        or section.line_end != len(source_lines)
        or not section.blocks
    ):
        return False
    cursor = 1
    for block in section.blocks:
        if (
            block.line_start != cursor
            or block.line_end > len(source_lines)
            or block.raw_text
            != "".join(source_lines[block.line_start - 1 : block.line_end])
        ):
            return False
        cursor = block.line_end + 1
    return cursor == len(source_lines) + 1


def _atomic_units(
    block: ParsedBlock, *, source_start: int | None
) -> tuple[_AtomicUnit, ...]:
    if block.kind == "table":
        boundaries = _descendants(block, "table_row")
    elif block.kind in {"bullet_list", "ordered_list"}:
        boundaries = tuple(
            child for child in block.children if child.kind == "list_item"
        )
    else:
        boundaries = block.children
    if not boundaries:
        return (
            _AtomicUnit(
                excerpt=_Excerpt(
                    block.raw_text,
                    block.line_start,
                    block.line_end,
                    source_start=source_start,
                    source_end=(
                        source_start + len(block.raw_text)
                        if source_start is not None
                        else None
                    ),
                ),
                block_kind=block.kind,
                warning_line_start=block.line_start,
                warning_line_end=block.line_end,
            ),
        )

    units: list[_AtomicUnit] = []
    for index, boundary in enumerate(boundaries):
        line_start = block.line_start if index == 0 else boundary.line_start
        line_end = (
            boundaries[index + 1].line_start - 1
            if index + 1 < len(boundaries)
            else block.line_end
        )
        units.append(
            _AtomicUnit(
                excerpt=_slice_excerpt_lines(
                    block,
                    line_start,
                    line_end,
                    source_start=source_start,
                ),
                block_kind=boundary.kind,
                warning_line_start=boundary.line_start,
                warning_line_end=boundary.line_end,
            )
        )
    return tuple(units)


def _trim_atomic_boundary_units(
    units: tuple[_AtomicUnit, ...],
) -> tuple[_AtomicUnit, ...]:
    trimmed_units: list[_AtomicUnit] = []
    for index, unit in enumerate(units):
        excerpt = _trim_excerpt_whitespace_lines(
            unit.excerpt,
            leading=index == 0,
            trailing=index == len(units) - 1,
        )
        if not excerpt.raw_text:
            continue
        trimmed_units.append(
            _AtomicUnit(
                excerpt=excerpt,
                block_kind=unit.block_kind,
                warning_line_start=max(unit.warning_line_start, excerpt.line_start),
                warning_line_end=min(unit.warning_line_end, excerpt.line_end),
            )
        )
    return tuple(trimmed_units)


def _trim_excerpt_whitespace_lines(
    excerpt: _Excerpt, *, leading: bool = True, trailing: bool = True
) -> _Excerpt:
    physical_lines = split_physical_lines(excerpt.raw_text)
    first = 0
    last = len(physical_lines)
    if leading:
        while first < last and not physical_lines[first].strip():
            first += 1
    if trailing:
        while last > first and not physical_lines[last - 1].strip():
            last -= 1
    start = sum(len(line) for line in physical_lines[:first])
    end = sum(len(line) for line in physical_lines[:last])
    return _slice_excerpt(excerpt, start, end)


def _descendants(block: ParsedBlock, kind: str) -> tuple[ParsedBlock, ...]:
    matches: list[ParsedBlock] = []
    pending = list(reversed(block.children))
    while pending:
        child = pending.pop()
        if child.kind == kind:
            matches.append(child)
        pending.extend(reversed(child.children))
    return tuple(matches)


def _slice_excerpt_lines(
    block: ParsedBlock,
    line_start: int,
    line_end: int,
    *,
    source_start: int | None,
) -> _Excerpt:
    physical_lines = split_physical_lines(block.raw_text)
    relative_start = line_start - block.line_start
    relative_end = line_end - block.line_start + 1
    start = sum(len(line) for line in physical_lines[:relative_start])
    end = sum(len(line) for line in physical_lines[:relative_end])
    return _Excerpt(
        raw_text=block.raw_text[start:end],
        line_start=line_start,
        line_end=line_end,
        source_start=(source_start + start if source_start is not None else None),
        source_end=(source_start + end if source_start is not None else None),
    )


def _with_warning(excerpt: _Excerpt, warning: ChunkWarning) -> _Excerpt:
    return _Excerpt(
        raw_text=excerpt.raw_text,
        line_start=excerpt.line_start,
        line_end=excerpt.line_end,
        warnings=(warning,),
        line_numbers=excerpt.line_numbers,
        source_start=excerpt.source_start,
        source_end=excerpt.source_end,
    )


def _slice_excerpt(excerpt: _Excerpt, start: int, end: int) -> _Excerpt:
    return _slice_excerpt_with_line_ends(
        excerpt, start, end, _physical_line_ends(excerpt.raw_text)
    )


def _physical_line_ends(raw_text: str) -> tuple[int, ...]:
    cumulative_ends: list[int] = []
    cursor = 0
    for physical_line in split_physical_lines(raw_text):
        cursor += len(physical_line)
        cumulative_ends.append(cursor)
    return tuple(cumulative_ends)


def _token_boundary(
    offsets: tuple[tuple[int, int], ...], index: int, text_length: int
) -> int:
    if index == 0:
        return 0
    if index == len(offsets):
        return text_length
    return offsets[index][0]


def _slice_excerpt_with_line_ends(
    excerpt: _Excerpt,
    start: int,
    end: int,
    cumulative_ends: tuple[int, ...],
) -> _Excerpt:
    raw_text = excerpt.raw_text[start:end]
    if not raw_text:
        return _Excerpt(
            raw_text="",
            line_start=excerpt.line_end,
            line_end=excerpt.line_end,
            source_start=excerpt.source_end,
            source_end=excerpt.source_end,
        )

    line_start_offset = bisect_right(cumulative_ends, start)
    line_end_offset = bisect_left(cumulative_ends, end)
    line_numbers = _excerpt_line_numbers(excerpt)[
        line_start_offset : line_end_offset + 1
    ]
    return _Excerpt(
        raw_text=raw_text,
        line_start=line_numbers[0],
        line_end=line_numbers[-1],
        warnings=excerpt.warnings,
        line_numbers=line_numbers,
        source_start=(
            excerpt.source_start + start if excerpt.source_start is not None else None
        ),
        source_end=(
            excerpt.source_start + end if excerpt.source_start is not None else None
        ),
    )


def chunk_config_identity_hash(
    config: ChunkConfig, descriptor: TokenizerDescriptor
) -> str:
    """Hash every approved coordinate that can change chunk boundaries.

    Args:
        config: Validated numeric child and parent token limits.
        descriptor: Exact tokenizer model, revision, library, and token policy.

    Returns:
        Lowercase hexadecimal SHA-256 digest of the canonical identity payload.
    """
    return config_hash(
        {
            "chunker_version": CHUNKER_VERSION,
            "target_tokens": config.target_tokens,
            "soft_max_tokens": config.soft_max_tokens,
            "overlap_tokens": config.overlap_tokens,
            "atomic_max_tokens": config.atomic_max_tokens,
            "parent_context_max_tokens": config.parent_context_max_tokens,
            "tokenizer_model_name": descriptor.model_name,
            "tokenizer_revision": descriptor.revision,
            "tokenizer_library_name": descriptor.library_name,
            "tokenizer_library_version": descriptor.library_version,
            "tokenizer_add_special_tokens": descriptor.add_special_tokens,
        }
    )
