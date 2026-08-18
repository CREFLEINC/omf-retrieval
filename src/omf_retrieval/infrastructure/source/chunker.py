"""Stable configuration identity for deterministic parent-child chunking."""

from omf_retrieval.application.indexing.hashing import config_hash
from omf_retrieval.application.indexing.ports import ChunkConfig, TokenizerDescriptor

CHUNKER_VERSION = "parent-child-v1"


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
