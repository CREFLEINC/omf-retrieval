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


_TOKEN_COUNTER_FAILED = object()
_PARSER_FAILED = object()
_MAX_WINDOW_SEARCH_CALLS = 1_024
_WINDOW_SEARCH_INPUT_MULTIPLIER = 128
_TINY_CORRECTIVE_REMAINDER_TOKENS = 64


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

    def build(
        self,
        section: ParsedSection,
        *,
        matched_raw_text: str,
        matched_line_start: int,
        matched_line_end: int,
        parser_version: str,
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
        if self._token_count(excerpt.raw_text) > (
            self._config.parent_context_max_tokens
        ):
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
            occurrence = _unique_occurrence(matched_lines_text, matched_raw_text)
            if occurrence is None:
                raise ValueError("Matched source is ambiguous in its line range")
            block_start = sum(
                len(line) for line in source_lines[: blocks[left].line_start - 1]
            )
            matched_start = matched_lines_start + occurrence - block_start
            return self._oversized_block_excerpt(
                excerpt,
                matched_start=matched_start,
                matched_end=matched_start + len(matched_raw_text),
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

    def _oversized_block_excerpt(
        self,
        excerpt: _Excerpt,
        *,
        matched_start: int,
        matched_end: int,
    ) -> _Excerpt:
        limit = self._config.parent_context_max_tokens
        if self._token_count(excerpt.raw_text[matched_start:matched_end]) > limit:
            raise ValueError("Matched source exceeds the parent context limit")
        offsets = self._token_offsets(excerpt.raw_text)
        left_candidates = sorted(
            {start for start, _ in offsets if start < matched_start}, reverse=True
        )
        right_candidates = sorted({end for _, end in offsets if end > matched_end})
        start = matched_start
        end = matched_end
        left_index = 0
        right_index = 0
        while left_index < len(left_candidates) or right_index < len(right_candidates):
            sides: list[tuple[int, int, str]] = []
            if left_index < len(left_candidates):
                sides.append((matched_start - left_candidates[left_index], 0, "left"))
            if right_index < len(right_candidates):
                sides.append((right_candidates[right_index] - matched_end, 1, "right"))
            added = False
            for _, _, side in sorted(sides):
                candidate_start = (
                    left_candidates[left_index] if side == "left" else start
                )
                candidate_end = (
                    right_candidates[right_index] if side == "right" else end
                )
                if (
                    self._token_count(excerpt.raw_text[candidate_start:candidate_end])
                    <= limit
                ):
                    start = candidate_start
                    end = candidate_end
                    if side == "left":
                        left_index += 1
                    else:
                        right_index += 1
                    added = True
                    break
                if side == "left":
                    left_index = len(left_candidates)
                else:
                    right_index = len(right_candidates)
            if not added:
                break
        return _slice_excerpt(excerpt, start, end)

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

    def split(
        self, section: ParsedSection, *, parser_version: str
    ) -> tuple[ChunkDraft, ...]:
        """Create source-backed children for one parsed Markdown section."""
        if (
            type(section) is not ParsedSection
            or type(parser_version) is not str
            or not parser_version.strip()
        ):
            raise ValueError("Invalid split input contract")
        if not section.body.strip():
            return ()

        heading_prefix = (
            "\n".join(section.heading_path) + "\n" if section.heading_path else ""
        )
        whole_section = _Excerpt(
            raw_text=section.body,
            line_start=min(block.line_start for block in section.blocks),
            line_end=max(block.line_end for block in section.blocks),
        )
        if self._token_count(heading_prefix + section.body) <= (
            self._config.soft_max_tokens
        ):
            excerpts = (whole_section,)
        else:
            excerpts = self._split_blocks(section.blocks, heading_prefix)
        return tuple(
            self._draft(
                excerpt,
                ordinal=ordinal,
                heading_prefix=heading_prefix,
                heading_path=section.heading_path,
                parser_version=parser_version,
            )
            for ordinal, excerpt in enumerate(excerpts)
        )

    def _split_blocks(
        self, blocks: tuple[ParsedBlock, ...], heading_prefix: str
    ) -> tuple[_Excerpt, ...]:
        window_limit: int | None = None
        chunks: list[_Excerpt] = []
        normal_blocks: list[ParsedBlock] = []
        separator_blocks: list[ParsedBlock] = []
        for block in blocks:
            if not block.raw_text.strip():
                if normal_blocks:
                    separator_blocks.append(block)
                continue
            if block.kind not in {"table", "bullet_list", "ordered_list", "blockquote"}:
                if separator_blocks:
                    candidate_blocks = (*normal_blocks, *separator_blocks, block)
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
                normal_blocks.append(block)
                continue
            separator_blocks.clear()
            if normal_blocks:
                chunks.extend(
                    self._split_normal_blocks(tuple(normal_blocks), heading_prefix)
                )
                normal_blocks.clear()
            chunks.extend(self._split_atomic_block(block, heading_prefix))
        if normal_blocks:
            chunks.extend(
                self._split_normal_blocks(tuple(normal_blocks), heading_prefix)
            )
        return tuple(chunks)

    def _split_atomic_block(
        self, block: ParsedBlock, heading_prefix: str
    ) -> tuple[_Excerpt, ...]:
        whole_block = _trim_excerpt_whitespace_lines(
            _Excerpt(block.raw_text, block.line_start, block.line_end)
        )
        if not whole_block.raw_text:
            return ()
        if self._token_count(heading_prefix + whole_block.raw_text) <= (
            self._config.atomic_max_tokens
        ):
            return (whole_block,)

        chunks: list[_Excerpt] = []
        pending: _Excerpt | None = None
        for unit in _trim_atomic_boundary_units(_atomic_units(block)):
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
        search_budget = _window_search_budget(unit.excerpt.raw_text, heading_prefix)

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
        self, blocks: tuple[ParsedBlock, ...], heading_prefix: str
    ) -> tuple[_Excerpt, ...]:
        if len(blocks) == 1:
            block = blocks[0]
            excerpt = _Excerpt(
                raw_text=block.raw_text,
                line_start=block.line_start,
                line_end=block.line_end,
            )
            chunks, pending = self._split_oversized_normal(excerpt, heading_prefix)
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
        for block in blocks:
            block_excerpt = _Excerpt(
                raw_text=block.raw_text,
                line_start=block.line_start,
                line_end=block.line_end,
            )
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
        search_budget = _window_search_budget(excerpt.raw_text, heading_prefix)

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
    ) -> int | None:
        candidate = min(start + source_budget, len(offsets))
        start_offset = _token_boundary(offsets, start, len(raw_text))
        available_tokens = candidate - start
        if available_tokens <= 0:
            return None

        remaining_tokens = len(offsets) - start
        minimum_progress = (
            1
            if remaining_tokens <= _TINY_CORRECTIVE_REMAINDER_TOKENS
            else min(
                available_tokens,
                max(1, 2 * self._config.overlap_tokens),
            )
        )
        probe_limit = available_tokens.bit_length() + 2
        probes = 0

        def fits(end: int) -> bool:
            nonlocal probes
            probes += 1
            if probes > probe_limit:
                raise ValueError("Tokenizer offsets cannot form a non-empty child")
            end_offset = _token_boundary(offsets, end, len(raw_text))
            probe_text = heading_prefix + raw_text[start_offset:end_offset]
            search_budget.consume(len(probe_text))
            return self._token_count(probe_text) <= token_limit

        if fits(candidate):
            return candidate

        lower = start + minimum_progress
        if lower == candidate or not fits(lower):
            return None

        upper = candidate
        while upper - lower > 1:
            midpoint = (lower + upper) // 2
            if fits(midpoint):
                lower = midpoint
            else:
                upper = midpoint
        return lower

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


def _window_search_budget(raw_text: str, heading_prefix: str) -> _WindowSearchBudget:
    return _WindowSearchBudget(
        calls_left=_MAX_WINDOW_SEARCH_CALLS,
        input_chars_left=_WINDOW_SEARCH_INPUT_MULTIPLIER
        * max(1, len(raw_text) + len(heading_prefix)),
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


def _atomic_units(block: ParsedBlock) -> tuple[_AtomicUnit, ...]:
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
                excerpt=_Excerpt(block.raw_text, block.line_start, block.line_end),
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
                excerpt=_slice_excerpt_lines(block, line_start, line_end),
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
    block: ParsedBlock, line_start: int, line_end: int
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
    )


def _with_warning(excerpt: _Excerpt, warning: ChunkWarning) -> _Excerpt:
    return _Excerpt(
        raw_text=excerpt.raw_text,
        line_start=excerpt.line_start,
        line_end=excerpt.line_end,
        warnings=(warning,),
        line_numbers=excerpt.line_numbers,
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
            raw_text="", line_start=excerpt.line_end, line_end=excerpt.line_end
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
