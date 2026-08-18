"""Unit tests for deterministic indexing hashes."""

import pytest

from omf_retrieval.application.indexing.hashing import (
    canonical_json,
    chunk_hash,
    config_hash,
    content_hash,
)


def test_canonical_json_sorts_keys_and_preserves_unicode() -> None:
    """Canonical JSON emits a stable, compact UTF-8 representation."""
    assert canonical_json({"문서": "값", "b": 2, "a": 1}) == (
        '{"a":1,"b":2,"문서":"값"}'.encode("utf-8")
    )


def test_config_hash_is_stable_across_key_order() -> None:
    """Equivalent mappings have the approved SHA-256 config digest."""
    assert config_hash({"b": 2, "a": 1}) == (
        "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    )
    assert config_hash({"b": 2, "a": 1}) == config_hash({"a": 1, "b": 2})


def test_content_hash_preserves_exact_utf8_bytes() -> None:
    """Content hashes change when raw source bytes change."""
    assert content_hash("문서\n".encode()) != content_hash("문서".encode())


def test_chunk_hash_includes_all_approved_coordinates() -> None:
    """Changing any approved chunk coordinate changes the digest."""
    expected = "d04e14ee90db90e47636d9ea0688390295bad6d8efda80b5e6517e026b13fb8a"
    baseline = chunk_hash(
        parser_version="markdown-v1",
        chunk_config_hash="config-abc",
        heading_path=("Design", "Indexing"),
        line_start=10,
        line_end=12,
        raw_text="원문\n",
        search_text="원문",
    )

    assert baseline == expected
    assert baseline != chunk_hash(
        parser_version="markdown-v2",
        chunk_config_hash="config-abc",
        heading_path=("Design", "Indexing"),
        line_start=10,
        line_end=12,
        raw_text="원문\n",
        search_text="원문",
    )
    assert baseline != chunk_hash(
        parser_version="markdown-v1",
        chunk_config_hash="config-def",
        heading_path=("Design", "Indexing"),
        line_start=10,
        line_end=12,
        raw_text="원문\n",
        search_text="원문",
    )
    assert baseline != chunk_hash(
        parser_version="markdown-v1",
        chunk_config_hash="config-abc",
        heading_path=("Design", "Chunking"),
        line_start=10,
        line_end=12,
        raw_text="원문\n",
        search_text="원문",
    )
    assert baseline != chunk_hash(
        parser_version="markdown-v1",
        chunk_config_hash="config-abc",
        heading_path=("Design", "Indexing"),
        line_start=11,
        line_end=12,
        raw_text="원문\n",
        search_text="원문",
    )
    assert baseline != chunk_hash(
        parser_version="markdown-v1",
        chunk_config_hash="config-abc",
        heading_path=("Design", "Indexing"),
        line_start=10,
        line_end=13,
        raw_text="원문\n",
        search_text="원문",
    )
    assert baseline != chunk_hash(
        parser_version="markdown-v1",
        chunk_config_hash="config-abc",
        heading_path=("Design", "Indexing"),
        line_start=10,
        line_end=12,
        raw_text="변경 원문\n",
        search_text="원문",
    )
    assert baseline != chunk_hash(
        parser_version="markdown-v1",
        chunk_config_hash="config-abc",
        heading_path=("Design", "Indexing"),
        line_start=10,
        line_end=12,
        raw_text="원문\n",
        search_text="검색 원문",
    )


def test_chunk_hash_requires_keyword_only_fields() -> None:
    """Chunk coordinates cannot be accidentally supplied positionally."""
    with pytest.raises(TypeError):
        chunk_hash("markdown-v1", "config-abc", ("Design",), 1, 1, "a", "a")
