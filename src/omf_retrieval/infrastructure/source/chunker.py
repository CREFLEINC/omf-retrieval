"""Deterministic source-backed parent-child chunking."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from omf_retrieval.application.indexing.hashing import chunk_hash, config_hash
from omf_retrieval.application.indexing.ports import (
    ChunkConfig,
    ChunkDraft,
    ChunkWarning,
    ParsedBlock,
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


@dataclass(frozen=True, slots=True)
class _AtomicUnit:
    excerpt: _Excerpt
    block_kind: str
    warning_line_start: int
    warning_line_end: int


_TOKEN_COUNTER_FAILED = object()


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
        chunks: list[_Excerpt] = []
        normal_blocks: list[ParsedBlock] = []
        for block in blocks:
            if block.kind not in {"table", "bullet_list", "ordered_list", "blockquote"}:
                normal_blocks.append(block)
                continue
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
        whole_block = _Excerpt(block.raw_text, block.line_start, block.line_end)
        if self._token_count(heading_prefix + block.raw_text) <= (
            self._config.atomic_max_tokens
        ):
            return (whole_block,)

        chunks: list[_Excerpt] = []
        pending: _Excerpt | None = None
        for unit in _atomic_units(block):
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
        chunks: list[_Excerpt] = []
        pending = unit.excerpt
        while self._token_count(heading_prefix + pending.raw_text) > (
            self._config.atomic_max_tokens
        ):
            piece = self._limit_prefix(
                pending, heading_prefix, self._config.atomic_max_tokens
            )
            chunks.append(_with_warning(piece, warning))
            pending = _slice_excerpt(
                pending, len(piece.raw_text), len(pending.raw_text)
            )
        chunks.append(_with_warning(pending, warning))
        return tuple(chunks)

    def _split_normal_blocks(
        self, blocks: tuple[ParsedBlock, ...], heading_prefix: str
    ) -> tuple[_Excerpt, ...]:
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
                pending_tokens = self._token_count(heading_prefix + pending.raw_text)
                candidate_tokens = self._token_count(
                    heading_prefix + candidate.raw_text
                )
                if candidate_tokens <= self._config.target_tokens or (
                    pending_tokens < self._config.target_tokens
                    and candidate_tokens <= self._config.soft_max_tokens
                ):
                    pending = candidate
                else:
                    chunks.append(pending)
                    pending = _join_excerpts(
                        self._overlap_suffix(pending), block_excerpt
                    )
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
        chunks: list[_Excerpt] = []
        pending = excerpt
        while self._token_count(heading_prefix + pending.raw_text) > (
            self._config.soft_max_tokens
        ):
            piece = self._target_prefix(pending, heading_prefix)
            chunks.append(piece)
            overlap = self._overlap_suffix(piece)
            pending = _slice_excerpt(
                pending,
                len(piece.raw_text) - len(overlap.raw_text),
                len(pending.raw_text),
            )
        return tuple(chunks), pending

    def _target_prefix(self, excerpt: _Excerpt, heading_prefix: str) -> _Excerpt:
        return self._limit_prefix(excerpt, heading_prefix, self._config.target_tokens)

    def _limit_prefix(
        self, excerpt: _Excerpt, heading_prefix: str, token_limit: int
    ) -> _Excerpt:
        offsets = self._token_offsets(excerpt.raw_text)
        prefix_tokens = self._token_count(heading_prefix)
        candidate_index = min(len(offsets), token_limit - prefix_tokens)
        if candidate_index <= 0:
            raise ValueError("Heading path leaves no room for child source text")

        while candidate_index > 0:
            end = (
                offsets[candidate_index][0]
                if candidate_index < len(offsets)
                else len(excerpt.raw_text)
            )
            if (
                self._token_count(heading_prefix + excerpt.raw_text[:end])
                <= token_limit
            ):
                return _slice_excerpt(excerpt, 0, end)
            candidate_index -= 1
        raise ValueError("Tokenizer offsets cannot form a non-empty child")

    def _overlap_suffix(self, excerpt: _Excerpt) -> _Excerpt:
        if self._config.overlap_tokens == 0:
            return _slice_excerpt(excerpt, len(excerpt.raw_text), len(excerpt.raw_text))
        offsets = self._token_offsets(excerpt.raw_text)
        overlap_count = min(self._config.overlap_tokens, len(offsets))
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
        materialized = _materialize_sequence(result)
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
        materialized = _materialize_sequence(result)
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


def _call_token_counter(operation: Callable[[str], object], text: str) -> object:
    try:
        return operation(text)
    except Exception:
        return _TOKEN_COUNTER_FAILED


def _materialize_sequence(result: object) -> object:
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes, bytearray)):
        return None
    try:
        declared_length = len(result)
        items = tuple(result)
    except Exception:
        return _TOKEN_COUNTER_FAILED
    return declared_length, items


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
    )


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
    )


def _slice_excerpt(excerpt: _Excerpt, start: int, end: int) -> _Excerpt:
    raw_text = excerpt.raw_text[start:end]
    if not raw_text:
        return _Excerpt(
            raw_text="", line_start=excerpt.line_end, line_end=excerpt.line_end
        )

    physical_lines = split_physical_lines(excerpt.raw_text)
    cumulative_ends: list[int] = []
    cursor = 0
    for physical_line in physical_lines:
        cursor += len(physical_line)
        cumulative_ends.append(cursor)
    line_start_offset = sum(line_end <= start for line_end in cumulative_ends)
    line_end_offset = next(
        index for index, line_end in enumerate(cumulative_ends) if end <= line_end
    )
    return _Excerpt(
        raw_text=raw_text,
        line_start=excerpt.line_start + line_start_offset,
        line_end=excerpt.line_start + line_end_offset,
        warnings=excerpt.warnings,
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
