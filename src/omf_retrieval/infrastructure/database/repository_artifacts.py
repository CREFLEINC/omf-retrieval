"""Persistence of reusable parse, chunk, and document-vector artifacts."""

from __future__ import annotations

import math
import re
import struct
from numbers import Real
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from omf_retrieval.application.indexing.artifact_identity import (
    parse_artifact_manifest,
)
from omf_retrieval.application.indexing.ports import (
    ChunkDraft,
    ParsedMarkdown,
    ParsedSection,
)
from omf_retrieval.application.indexing.service import ParseArtifacts, StoredChunk
from omf_retrieval.domain.models import EmbeddingDescriptor
from omf_retrieval.infrastructure.database.models import (
    Chunk,
    ChunkEmbedding,
    DocumentParse,
    Section,
)
from omf_retrieval.infrastructure.database.repository_errors import (
    RepositoryInvariantError,
)

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class ReusableArtifactStore:
    """Store reusable parser and vector outputs behind one bound config."""

    def __init__(
        self,
        session: Session,
        descriptor: EmbeddingDescriptor,
        embedding_config_hash: str,
    ) -> None:
        self._session = session
        self._descriptor = descriptor
        self._embedding_config_hash = embedding_config_hash

    def find_parse(
        self,
        content_id: UUID,
        parser_version: str,
        chunk_config_hash: str,
    ) -> ParseArtifacts | None:
        """Load reusable section and chunk artifacts for one parse identity."""
        _require_sha256(chunk_config_hash, "chunk_config_hash")
        parse = self._session.scalar(
            select(DocumentParse).where(
                DocumentParse.content_id == content_id,
                DocumentParse.parser_version == parser_version,
                DocumentParse.chunk_config_hash == chunk_config_hash,
            )
        )
        if parse is None:
            return None
        section_rows = tuple(
            self._session.scalars(
                select(Section)
                .where(Section.parse_id == parse.id)
                .order_by(Section.ordinal)
            )
        )
        ordinal_by_id = {section.id: section.ordinal for section in section_rows}
        if any(
            section.parent_section_id is not None
            and section.parent_section_id not in ordinal_by_id
            for section in section_rows
        ):
            raise RepositoryInvariantError(
                "stored parse hierarchy violates the application contract"
            )
        sections = tuple(
            ParsedSection(
                ordinal=section.ordinal,
                parent_ordinal=(
                    None
                    if section.parent_section_id is None
                    else ordinal_by_id.get(section.parent_section_id)
                ),
                level=section.level,
                heading=section.heading,
                heading_path=tuple(section.heading_path),
                body=section.body,
                line_start=section.line_start,
                line_end=section.line_end,
                blocks=(),
            )
            for section in section_rows
        )
        chunk_rows = tuple(
            self._session.execute(
                select(Chunk, Section.ordinal)
                .join(Section, Chunk.section_id == Section.id)
                .where(Section.parse_id == parse.id)
                .order_by(Section.ordinal, Chunk.ordinal)
            )
        )
        chunks = tuple(
            StoredChunk(
                chunk_id=chunk.id,
                draft=ChunkDraft(
                    ordinal=chunk.ordinal,
                    raw_text=chunk.raw_text,
                    search_text=chunk.search_text,
                    token_count=chunk.token_count,
                    line_start=chunk.line_start,
                    line_end=chunk.line_end,
                    chunk_hash=chunk.chunk_hash,
                ),
            )
            for chunk, _ in chunk_rows
        )
        try:
            parsed = ParsedMarkdown(
                parser_version=parse.parser_version, sections=sections
            )
        except ValueError as error:
            raise RepositoryInvariantError(
                "stored parse hierarchy violates the application contract"
            ) from error
        try:
            manifest = parse_artifact_manifest(
                sections,
                tuple(chunk.draft for chunk in chunks),
                tuple(section_ordinal for _, section_ordinal in chunk_rows),
            )
        except ValueError as error:
            raise RepositoryInvariantError(
                "stored parse artifact manifest violates the application contract"
            ) from error
        if (
            type(parse.section_count) is not int
            or type(parse.chunk_count) is not int
            or type(parse.artifact_hash) is not str
            or parse.section_count != manifest.section_count
            or parse.chunk_count != manifest.chunk_count
            or parse.artifact_hash != manifest.artifact_hash
        ):
            raise RepositoryInvariantError(
                "stored parse artifact manifest does not match persisted artifacts"
            )
        return ParseArtifacts(
            parser_version=parse.parser_version,
            chunk_config_hash=parse.chunk_config_hash,
            parsed=parsed,
            chunks=chunks,
        )

    def save_parse(
        self,
        content_id: UUID,
        parser_version: str,
        chunk_config_hash: str,
        parsed: ParsedMarkdown,
        chunks: tuple[ChunkDraft, ...],
    ) -> ParseArtifacts:
        """Persist a deterministic parse once and reject conflicting replay.

        Blocks and nested children are transient parser output owned by
        ``parser_version``. Exact replay compares only persisted section and
        chunk fields; changing block behavior requires a parser-version bump.
        """
        _require_sha256(chunk_config_hash, "chunk_config_hash")
        if (
            type(parsed) is not ParsedMarkdown
            or parsed.parser_version != parser_version
        ):
            raise RepositoryInvariantError(
                "parsed document has a mismatched parser identity"
            )
        section_for_chunk = _validate_and_map_chunks(parsed.sections, chunks)
        try:
            incoming_manifest = parse_artifact_manifest(
                parsed.sections,
                chunks,
                section_for_chunk,
            )
        except ValueError as error:
            raise RepositoryInvariantError(
                "parse artifact manifest violates the application contract"
            ) from error
        existing = self.find_parse(content_id, parser_version, chunk_config_hash)
        if existing is not None:
            if not _parse_replay_matches(existing, parsed, chunks):
                raise RepositoryInvariantError(
                    "conflicting parse replay changed persisted section or chunk output"
                )
            return existing
        parse_id = self._session.scalar(
            insert(DocumentParse)
            .values(
                id=uuid4(),
                content_id=content_id,
                parser_version=parser_version,
                chunk_config_hash=chunk_config_hash,
                section_count=incoming_manifest.section_count,
                chunk_count=incoming_manifest.chunk_count,
                artifact_hash=incoming_manifest.artifact_hash,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    DocumentParse.content_id,
                    DocumentParse.parser_version,
                    DocumentParse.chunk_config_hash,
                ]
            )
            .returning(DocumentParse.id)
        )
        if parse_id is None:
            reused = self.find_parse(content_id, parser_version, chunk_config_hash)
            if reused is None:
                raise RepositoryInvariantError("parse upsert did not resolve artifacts")
            if not _parse_replay_matches(reused, parsed, chunks):
                raise RepositoryInvariantError(
                    "conflicting parse replay changed persisted section or chunk output"
                )
            return reused
        stored_sections: dict[int, Section] = {}
        for section in parsed.sections:
            parent = (
                None
                if section.parent_ordinal is None
                else stored_sections[section.parent_ordinal]
            )
            row = Section(
                parse_id=parse_id,
                parent_section_id=None if parent is None else parent.id,
                ordinal=section.ordinal,
                level=section.level,
                heading=section.heading,
                heading_path=list(section.heading_path),
                body=section.body,
                line_start=section.line_start,
                line_end=section.line_end,
            )
            self._session.add(row)
            self._session.flush()
            stored_sections[section.ordinal] = row
        for chunk, section_ordinal in zip(chunks, section_for_chunk, strict=True):
            self._session.add(
                Chunk(
                    section_id=stored_sections[section_ordinal].id,
                    ordinal=chunk.ordinal,
                    raw_text=chunk.raw_text,
                    search_text=chunk.search_text,
                    token_count=chunk.token_count,
                    line_start=chunk.line_start,
                    line_end=chunk.line_end,
                    chunk_hash=chunk.chunk_hash,
                )
            )
        self._session.flush()
        stored = self.find_parse(content_id, parser_version, chunk_config_hash)
        if stored is None:
            raise RepositoryInvariantError("saved parse could not be reconstructed")
        return stored

    def find_embedding(
        self,
        chunk_id: UUID,
        embedding_config_hash: str,
    ) -> tuple[float, ...] | None:
        """Load the vector linked to one exact chunk and embedding config."""
        self._require_embedding_config_hash(embedding_config_hash)
        row = self._session.scalar(
            select(ChunkEmbedding).where(
                ChunkEmbedding.chunk_id == chunk_id,
                ChunkEmbedding.embedding_config_hash == embedding_config_hash,
            )
        )
        return None if row is None else self._validated_stored_vector(row)

    def find_reusable_embedding(
        self,
        chunk_hash: str,
        embedding_config_hash: str,
    ) -> tuple[float, ...] | None:
        """Load one calculation shared by identical chunk content and config."""
        _require_sha256(chunk_hash, "chunk_hash")
        self._require_embedding_config_hash(embedding_config_hash)
        rows = tuple(
            self._session.scalars(
                select(ChunkEmbedding)
                .join(Chunk, ChunkEmbedding.chunk_id == Chunk.id)
                .where(
                    Chunk.chunk_hash == chunk_hash,
                    ChunkEmbedding.embedding_config_hash == embedding_config_hash,
                )
                .order_by(ChunkEmbedding.id)
            )
        )
        if not rows:
            return None
        vectors = tuple(self._validated_stored_vector(row) for row in rows)
        if any(vectors[0] != vector for vector in vectors[1:]):
            raise RepositoryInvariantError(
                "reusable embedding identity resolved conflicting vectors"
            )
        return vectors[0]

    def save_embedding(
        self,
        chunk_id: UUID,
        embedding_config_hash: str,
        vector: tuple[float, ...],
    ) -> None:
        """Persist or idempotently link an exact float32 document vector."""
        normalized = self._normalize_vector(vector)
        self._require_embedding_config_hash(embedding_config_hash)
        if type(chunk_id) is not UUID:
            raise RepositoryInvariantError("chunk_id must be an exact UUID")
        existing_vector = self.find_embedding(chunk_id, embedding_config_hash)
        if existing_vector is not None:
            if existing_vector != normalized:
                raise RepositoryInvariantError(
                    "embedding identity already links a different vector"
                )
            return
        chunk = self._session.get(Chunk, chunk_id)
        if chunk is None:
            raise RepositoryInvariantError("chunk_id does not resolve")
        reusable = self.find_reusable_embedding(
            chunk.chunk_hash,
            embedding_config_hash,
        )
        if reusable is not None and reusable != normalized:
            raise RepositoryInvariantError(
                "shared chunk hash already has a different document vector"
            )
        descriptor = self._descriptor
        created_id = self._session.scalar(
            insert(ChunkEmbedding)
            .values(
                id=uuid4(),
                chunk_id=chunk_id,
                embedding_config_hash=embedding_config_hash,
                model_name=descriptor.model_name,
                model_revision=descriptor.revision,
                dimension=descriptor.dimension,
                embedding=list(normalized),
                status="ready",
            )
            .on_conflict_do_nothing(
                index_elements=[
                    ChunkEmbedding.chunk_id,
                    ChunkEmbedding.embedding_config_hash,
                ]
            )
            .returning(ChunkEmbedding.id)
        )
        if created_id is not None:
            return
        existing = self._session.scalar(
            select(ChunkEmbedding).where(
                ChunkEmbedding.chunk_id == chunk_id,
                ChunkEmbedding.embedding_config_hash == embedding_config_hash,
            )
        )
        if existing is None:
            raise RepositoryInvariantError(
                "embedding upsert did not resolve a chunk link"
            )
        stored = self._validated_stored_vector(existing)
        if stored != normalized:
            raise RepositoryInvariantError(
                "embedding identity already links a different vector"
            )

    def _normalize_vector(self, vector: tuple[float, ...]) -> tuple[float, ...]:
        """Canonicalize coordinates to pgvector's deterministic float32 values."""
        if type(vector) is not tuple:
            raise RepositoryInvariantError("embedding vector must be an exact tuple")
        if len(vector) != self._descriptor.dimension:
            raise RepositoryInvariantError("embedding vector dimension is mismatched")
        if any(
            isinstance(coordinate, bool)
            or not isinstance(coordinate, Real)
            or not math.isfinite(float(coordinate))
            for coordinate in vector
        ):
            raise RepositoryInvariantError(
                "embedding coordinates must be finite real numbers"
            )
        try:
            return tuple(
                struct.unpack("!f", struct.pack("!f", float(coordinate)))[0]
                for coordinate in vector
            )
        except OverflowError as error:
            raise RepositoryInvariantError(
                "embedding coordinates must fit PostgreSQL vector float32"
            ) from error

    def _require_embedding_config_hash(self, value: object) -> None:
        _require_sha256(value, "embedding_config_hash")
        if value != self._embedding_config_hash:
            raise RepositoryInvariantError(
                "embedding_config_hash mismatches the bound IndexConfig"
            )

    def _validated_stored_vector(
        self,
        row: ChunkEmbedding,
    ) -> tuple[float, ...]:
        descriptor = self._descriptor
        if (
            row.model_name != descriptor.model_name
            or row.model_revision != descriptor.revision
            or row.dimension != descriptor.dimension
            or row.status != "ready"
        ):
            raise RepositoryInvariantError(
                "stored embedding identity mismatches repository configuration"
            )
        return self._normalize_vector(tuple(row.embedding))


def _require_sha256(value: object, field: str) -> None:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        raise RepositoryInvariantError(f"{field} must be a lowercase SHA-256 hash")


def _validate_and_map_chunks(
    sections: tuple[ParsedSection, ...],
    chunks: tuple[ChunkDraft, ...],
) -> tuple[int, ...]:
    if type(chunks) is not tuple or not all(
        type(chunk) is ChunkDraft for chunk in chunks
    ):
        raise RepositoryInvariantError("chunks must be an exact ChunkDraft tuple")
    mapped: list[int] = []
    ordinals_by_section: dict[int, list[int]] = {
        section.ordinal: [] for section in sections
    }
    for chunk in chunks:
        candidates = tuple(
            section.ordinal
            for section in sections
            if section.line_start <= chunk.line_start
            and chunk.line_end <= section.line_end
        )
        if len(candidates) != 1:
            raise RepositoryInvariantError(
                "chunk line range must map to exactly one parsed section"
            )
        section_ordinal = candidates[0]
        mapped.append(section_ordinal)
        ordinals_by_section[section_ordinal].append(chunk.ordinal)
    if any(
        ordinals != list(range(len(ordinals)))
        for ordinals in ordinals_by_section.values()
    ):
        raise RepositoryInvariantError(
            "chunk ordinal must be sequential within each parsed section"
        )
    if mapped != sorted(mapped):
        raise RepositoryInvariantError("chunks must follow parsed section order")
    return tuple(mapped)


def _parse_replay_matches(
    stored: ParseArtifacts,
    parsed: ParsedMarkdown,
    chunks: tuple[ChunkDraft, ...],
) -> bool:
    """Compare persisted projection manifests while excluding transient blocks."""
    if stored.parser_version != parsed.parser_version:
        return False
    stored_chunks = tuple(chunk.draft for chunk in stored.chunks)
    try:
        stored_manifest = parse_artifact_manifest(
            stored.parsed.sections,
            stored_chunks,
            _validate_and_map_chunks(stored.parsed.sections, stored_chunks),
        )
        replay_manifest = parse_artifact_manifest(
            parsed.sections,
            chunks,
            _validate_and_map_chunks(parsed.sections, chunks),
        )
    except (RepositoryInvariantError, ValueError):
        return False
    return stored_manifest == replay_manifest


__all__ = ["ReusableArtifactStore"]
