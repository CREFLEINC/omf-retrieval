"""Canonical identity for persisted parse and chunk projections."""

from dataclasses import dataclass

from omf_retrieval.application.indexing.hashing import config_hash
from omf_retrieval.application.indexing.ports import ChunkDraft, ParsedSection

PARSE_ARTIFACT_MANIFEST_VERSION = "document-parse-artifacts-v1"


@dataclass(frozen=True, slots=True)
class ParseArtifactManifest:
    """Counts and digest covering every persisted parse artifact field."""

    section_count: int
    chunk_count: int
    artifact_hash: str


def parse_artifact_manifest(
    sections: tuple[ParsedSection, ...],
    chunks: tuple[ChunkDraft, ...],
    owning_section_ordinals: tuple[int, ...],
) -> ParseArtifactManifest:
    """Build the versioned, ID-free identity of one persisted projection."""
    if type(sections) is not tuple or not all(
        type(section) is ParsedSection for section in sections
    ):
        raise ValueError("artifact sections must be an exact ParsedSection tuple")
    if not sections:
        raise ValueError("a persisted parse requires at least one section")
    if any(section.ordinal != expected for expected, section in enumerate(sections)):
        raise ValueError("artifact section order must be exact and sequential")
    if type(chunks) is not tuple or not all(
        type(chunk) is ChunkDraft for chunk in chunks
    ):
        raise ValueError("artifact chunks must be an exact ChunkDraft tuple")
    if (
        type(owning_section_ordinals) is not tuple
        or len(owning_section_ordinals) != len(chunks)
        or any(type(ordinal) is not int for ordinal in owning_section_ordinals)
    ):
        raise ValueError("artifact chunk owners must be an exact integer tuple")

    chunk_counts = [0] * len(sections)
    previous_owner = -1
    expected_chunk_ordinal = 0
    chunk_payloads: list[dict[str, object]] = []
    for owner, chunk in zip(owning_section_ordinals, chunks, strict=True):
        if owner < 0 or owner >= len(sections) or owner < previous_owner:
            raise ValueError("artifact chunk order must follow section order")
        if owner != previous_owner:
            expected_chunk_ordinal = 0
        if chunk.ordinal != expected_chunk_ordinal:
            raise ValueError("artifact chunk order must be exact and sequential")
        chunk_counts[owner] += 1
        previous_owner = owner
        expected_chunk_ordinal += 1
        chunk_payloads.append(
            {
                "section_ordinal": owner,
                "ordinal": chunk.ordinal,
                "raw_text": chunk.raw_text,
                "search_text": chunk.search_text,
                "token_count": chunk.token_count,
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
                "chunk_hash": chunk.chunk_hash,
            }
        )

    for section, chunk_count in zip(sections, chunk_counts, strict=True):
        if section.body.strip():
            if chunk_count == 0:
                raise ValueError("searchable section requires at least one chunk")
        elif chunk_count != 0:
            raise ValueError("blank section must not persist chunks")

    payload = {
        "version": PARSE_ARTIFACT_MANIFEST_VERSION,
        "sections": [
            {
                "ordinal": section.ordinal,
                "parent_ordinal": section.parent_ordinal,
                "level": section.level,
                "heading": section.heading,
                "heading_path": list(section.heading_path),
                "body": section.body,
                "line_start": section.line_start,
                "line_end": section.line_end,
            }
            for section in sections
        ],
        "chunks": chunk_payloads,
    }
    return ParseArtifactManifest(
        section_count=len(sections),
        chunk_count=len(chunks),
        artifact_hash=config_hash(payload),
    )


__all__ = [
    "PARSE_ARTIFACT_MANIFEST_VERSION",
    "ParseArtifactManifest",
    "parse_artifact_manifest",
]
