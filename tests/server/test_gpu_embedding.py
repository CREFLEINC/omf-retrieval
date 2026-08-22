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


@pytest.mark.gpu
def test_real_qwen_gpu_embedding_and_source_offsets(tmp_path: Path) -> None:
    """Verify CUDA vectors and Korean/emoji byte-fallback offsets on phoebe."""
    cache = Path(os.environ["OMF_RETRIEVAL_GPU_MODEL_CACHE"])
    password = tmp_path / "postgres-password"
    audit_key = tmp_path / "audit-key"
    password.write_bytes(b"checkpoint-only")
    audit_key.write_bytes(b"checkpoint-only")
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
    vector = provider.embed_documents((text,))[0]
    token_ids = counter.encode(text)
    offsets = counter.offsets(text)

    assert len(vector) == 1024
    assert isclose(sqrt(sum(value * value for value in vector)), 1.0, abs_tol=1e-5)
    assert len(token_ids) == len(offsets) > 0
    assert all(text[start:end] for start, end in offsets)
    assert all(
        left[1] <= right[0] for left, right in zip(offsets, offsets[1:], strict=False)
    )
