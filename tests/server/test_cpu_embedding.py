"""Optional local checkpoint for the real pinned Qwen CPU adapter."""

import os
from math import isclose, sqrt
from pathlib import Path

import pytest

from omf_retrieval.application.indexing.ports import TokenizerDescriptor
from omf_retrieval.infrastructure.embedding import (
    SentenceTransformerEmbeddingProvider,
    SentenceTransformerTokenCounter,
)
from omf_retrieval.infrastructure.source.chunker import ParentChildChunker
from omf_retrieval.infrastructure.source.markdown import MarkdownItParser
from omf_retrieval.settings import Settings


class _RecordingTokenCounter:
    """Record only offset contexts that the real chunker actually requested."""

    def __init__(self, delegate: SentenceTransformerTokenCounter) -> None:
        self._delegate = delegate
        self.contexts: list[tuple[str, frozenset[int]]] = []

    @property
    def descriptor(self) -> TokenizerDescriptor:
        return self._delegate.descriptor

    def encode(self, text: str) -> tuple[int, ...]:
        return self._delegate.encode(text)

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        offsets = self._delegate.offsets(text)
        self.contexts.append((text, _group_character_boundaries(offsets, len(text))))
        return offsets

    def clear(self) -> None:
        self.contexts.clear()


def _offsets_form_safe_groups(offsets: tuple[tuple[int, int], ...]) -> bool:
    group_end = offsets[0][1]
    previous_start = offsets[0][0]
    for start, end in offsets[1:]:
        if start < previous_start:
            return False
        if start < group_end:
            group_end = max(group_end, end)
        else:
            group_end = end
        previous_start = start
    return True


def _group_character_boundaries(
    offsets: tuple[tuple[int, int], ...], text_length: int
) -> frozenset[int]:
    boundaries = {0, text_length}
    if not offsets:
        return frozenset(boundaries)
    group_start, group_end = offsets[0]
    for start, end in offsets[1:]:
        if start < group_end:
            group_end = max(group_end, end)
        else:
            boundaries.update((group_start, group_end))
            group_start, group_end = start, end
    boundaries.update((group_start, group_end))
    return frozenset(boundaries)


def _has_safe_source_occurrence(
    source: str, excerpt: str, boundaries: frozenset[int]
) -> bool:
    cursor = source.find(excerpt)
    while cursor >= 0:
        if cursor in boundaries and cursor + len(excerpt) in boundaries:
            return True
        cursor = source.find(excerpt, cursor + 1)
    return False


def test_context_dependent_token_boundaries_use_materialized_child_context() -> None:
    """Freeze the observed table suffix shift without requiring the checkpoint."""
    section = "가" * 791
    child = section[1:790]
    full_section_boundaries = _group_character_boundaries(
        ((0, 1), (1, 787), (787, 791)), len(section)
    )
    child_boundaries = _group_character_boundaries(((0, 786), (786, 789)), len(child))

    assert not _has_safe_source_occurrence(section, child, full_section_boundaries)
    assert _has_safe_source_occurrence(child, child, child_boundaries)


def test_real_qwen_cpu_embedding_and_korean_byte_fallback() -> None:
    """Exercise both embedding modes and the real fast-tokenizer offset shape."""
    raw_cache = os.environ.get("OMF_RETRIEVAL_CPU_MODEL_CACHE")
    if raw_cache is None:
        pytest.skip("OMF_RETRIEVAL_CPU_MODEL_CACHE is not configured")
    settings = Settings(
        environment="test",
        embedding_device="cpu",
        embedding_cache_dir=Path(raw_cache),
    )
    provider = SentenceTransformerEmbeddingProvider(settings)
    counter = SentenceTransformerTokenCounter(settings)
    text = "한국어 설계 문서\nQwen byte-fallback 검증"

    assert provider.is_ready()
    document_vector = provider.embed_documents((text,))[0]
    query_vector = provider.embed_query(text)
    token_ids = counter.encode(text)
    offsets = counter.offsets(text)

    assert len(document_vector) == len(query_vector) == 1024
    assert all(
        isclose(sqrt(sum(value * value for value in vector)), 1.0, abs_tol=1e-12)
        for vector in (document_vector, query_vector)
    )
    assert len(token_ids) == len(offsets) > 0
    assert all(text[start:end] for start, end in offsets)
    assert _offsets_form_safe_groups(offsets)

    title = "OMF-MES 설계 위키 — 카탈로그"
    title_offsets = counter.offsets(title + "\n")
    assert any(
        right[0] < left[1] and right[1] <= left[1]
        for left, right in zip(title_offsets, title_offsets[1:], strict=False)
    )
    source = f"# {title}\n\n본문\n"
    parsed = MarkdownItParser().parse(source)
    section = parsed.sections[-1]
    chunks = ParentChildChunker(counter, counter.descriptor).split(
        section, parser_version=parsed.parser_version
    )

    assert chunks
    assert all(
        chunk.token_count == len(counter.encode(chunk.search_text)) for chunk in chunks
    )


def test_real_qwen_cpu_chunks_every_fixed_omf_index_section() -> None:
    """Run the full observed 00-index through real tokenize and chunk paths."""
    raw_cache = os.environ.get("OMF_RETRIEVAL_CPU_MODEL_CACHE")
    raw_source_root = os.environ.get("OMF_RETRIEVAL_SOURCE_REPO")
    if raw_cache is None or raw_source_root is None:
        pytest.skip("CPU model cache or fixed source is not configured")
    source_path = Path(raw_source_root) / "design/wiki/00-index.md"
    if not source_path.is_file():
        pytest.skip("fixed 00-index source is not available")
    settings = Settings(
        environment="test",
        embedding_device="cpu",
        embedding_cache_dir=Path(raw_cache),
    )
    counter = SentenceTransformerTokenCounter(settings)
    recording_counter = _RecordingTokenCounter(counter)
    source = source_path.read_text(encoding="utf-8")
    parsed = MarkdownItParser().parse(source)
    chunker = ParentChildChunker(recording_counter, recording_counter.descriptor)
    chunk_count = 0

    for section in parsed.sections:
        recording_counter.clear()
        chunks = chunker.split(section, parser_version=parsed.parser_version)
        if not section.body.strip():
            assert chunks == ()
            continue
        for chunk in chunks:
            assert chunk.raw_text
            assert (
                section.line_start
                <= chunk.line_start
                <= chunk.line_end
                <= section.line_end
            )
            assert chunk.token_count == len(counter.encode(chunk.search_text))
            assert chunk.token_count <= 800
            raw_offsets = counter.offsets(chunk.raw_text)
            search_offsets = counter.offsets(chunk.search_text)
            assert raw_offsets and search_offsets
            assert _offsets_form_safe_groups(raw_offsets)
            assert _offsets_form_safe_groups(search_offsets)
            matching_contexts = tuple(
                (context, boundaries)
                for context, boundaries in recording_counter.contexts
                if chunk.raw_text in context
            )
            if matching_contexts:
                assert any(
                    _has_safe_source_occurrence(context, chunk.raw_text, boundaries)
                    for context, boundaries in matching_contexts
                )
        chunk_count += len(chunks)

    assert chunk_count > 0
