"""Deterministic hashes used to identify indexed source material."""

import hashlib
import json


def canonical_json(value: object) -> bytes:
    """Serialize a JSON-compatible value into stable UTF-8 bytes.

    Args:
        value: The JSON-compatible value to serialize.

    Returns:
        A compact UTF-8 canonical JSON representation.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def config_hash(value: object) -> str:
    """Return the SHA-256 digest of canonical configuration JSON.

    Args:
        value: The JSON-compatible configuration value.

    Returns:
        The lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(canonical_json(value)).hexdigest()


def content_hash(content: bytes) -> str:
    """Return the SHA-256 digest of unmodified source bytes.

    Args:
        content: Raw bytes read from the source archive.

    Returns:
        The lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(content).hexdigest()


def chunk_hash(
    *,
    parser_version: str,
    chunk_config_hash: str,
    heading_path: tuple[str, ...],
    line_start: int,
    line_end: int,
    raw_text: str,
    search_text: str,
) -> str:
    """Return a digest for the full approved chunk identity.

    Args:
        parser_version: Version of the parser that produced the chunk.
        chunk_config_hash: Digest of the chunking configuration.
        heading_path: Ordered Markdown heading hierarchy.
        line_start: One-based first source line of the chunk.
        line_end: One-based last source line of the chunk.
        raw_text: Exact source text in the chunk.
        search_text: Text used for search indexing.

    Returns:
        The lowercase hexadecimal SHA-256 digest.
    """
    return config_hash(
        {
            "parser_version": parser_version,
            "chunk_config_hash": chunk_config_hash,
            "heading_path": heading_path,
            "line_start": line_start,
            "line_end": line_end,
            "raw_text": raw_text,
            "search_text": search_text,
        }
    )
