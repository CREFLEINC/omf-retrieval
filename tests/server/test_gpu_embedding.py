"""External checkpoint for the real pinned Qwen GPU adapter."""

import os
from math import isclose, sqrt
from pathlib import Path

import pytest

from omf_retrieval.infrastructure.embedding import (
    SentenceTransformerEmbeddingProvider,
    SentenceTransformerTokenCounter,
)
from omf_retrieval.settings import Settings


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


@pytest.mark.gpu
def test_real_qwen_gpu_embedding_and_source_offsets(tmp_path: Path) -> None:
    """Verify CUDA vectors and Korean/emoji byte-fallback offsets on phoebe."""
    cache = Path(os.environ["OMF_RETRIEVAL_GPU_MODEL_CACHE"])
    password = tmp_path / "postgres-password"
    audit_key = tmp_path / "audit-key"
    password.write_bytes(b"checkpoint-only")
    audit_key.write_bytes(b"checkpoint-only")
    audit_key.chmod(0o600)
    settings = Settings(
        environment="production",
        embedding_device="cuda:0",
        embedding_cache_dir=cache,
        postgres_password_file=password,
        audit_hmac_key_file=audit_key,
    )
    provider = SentenceTransformerEmbeddingProvider(settings)
    counter = SentenceTransformerTokenCounter(settings)
    text = "한국어 설계\nemoji 🙂 byte-fallback"

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
