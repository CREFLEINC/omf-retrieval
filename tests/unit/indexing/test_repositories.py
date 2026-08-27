"""Unit tests for PostgreSQL indexing-repository invariants."""

from inspect import signature
from typing import get_type_hints
from unittest.mock import Mock
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from omf_retrieval.application.indexing.metadata import DocumentRelationSpec
from omf_retrieval.application.indexing.ports import (
    ChunkDraft,
    ParsedBlock,
    ParsedMarkdown,
    ParsedSection,
)
from omf_retrieval.application.indexing.service import (
    ParseArtifacts,
    StoredChunk,
    _Repository,
)
from omf_retrieval.domain.enums import RelationType
from omf_retrieval.domain.models import EmbeddingDescriptor, LineRange
from omf_retrieval.infrastructure.database.models import (
    Chunk,
    DocumentOccurrence,
    DocumentRelation,
    IndexConfig,
    IndexRun,
)
from omf_retrieval.infrastructure.database.repositories import (
    PostgresIndexRepository,
    RepositoryInvariantError,
    _advisory_lock_key,
)
from omf_retrieval.infrastructure.database.repository_config import (
    EmbeddingAdapterIdentity,
    document_embedding_config_hash,
    embedding_config_snapshot,
    full_index_config_hash,
)

SOURCE_ID = UUID("10000000-0000-0000-0000-000000000001")
OTHER_SOURCE_ID = UUID("10000000-0000-0000-0000-000000000002")
CONFIG_ID = UUID("20000000-0000-0000-0000-000000000001")
CHUNK_ID = UUID("30000000-0000-0000-0000-000000000001")


def _bound_embedding_hash() -> str:
    return document_embedding_config_hash(
        embedding_config_snapshot(
            EmbeddingDescriptor("test/model", "revision-1", 3),
            EmbeddingAdapterIdentity(
                provider="sentence-transformers",
                normalize_embeddings=True,
                library_name="sentence-transformers",
                library_version="5.7.0",
            ),
            "Instruct: {query}",
        )
    )


def _repository(
    session: Session | Mock,
    *,
    descriptor: EmbeddingDescriptor | None = None,
    tamper_config_hash: bool = False,
) -> PostgresIndexRepository:
    descriptor = descriptor or EmbeddingDescriptor("test/model", "revision-1", 3)
    adapter = EmbeddingAdapterIdentity(
        provider="sentence-transformers",
        normalize_embeddings=True,
        library_name="sentence-transformers",
        library_version="5.7.0",
    )
    parser_config: dict[str, object] = {}
    chunk_config: dict[str, object] = {}
    tokenizer_config: dict[str, object] = {}
    embedding_config = embedding_config_snapshot(
        EmbeddingDescriptor("test/model", "revision-1", 3),
        adapter,
        "Instruct: {query}",
    )
    rrf_config: dict[str, object] = {}
    stored_config = IndexConfig(
        id=CONFIG_ID,
        config_hash=(
            "f" * 64
            if tamper_config_hash
            else full_index_config_hash(
                parser_config=parser_config,
                chunk_config=chunk_config,
                tokenizer_config=tokenizer_config,
                embedding_config=embedding_config,
                rrf_config=rrf_config,
            )
        ),
        parser_config=parser_config,
        chunk_config=chunk_config,
        tokenizer_config=tokenizer_config,
        embedding_config=embedding_config,
        rrf_config=rrf_config,
    )
    if isinstance(session, Mock):
        session.get.side_effect = lambda model, identifier: (
            stored_config if model is IndexConfig and identifier == CONFIG_ID else None
        )
    return PostgresIndexRepository(
        session=session,
        source_profile_id=SOURCE_ID,
        index_config_id=CONFIG_ID,
        embedding_descriptor=descriptor,
        embedding_adapter_identity=adapter,
    )


def test_advisory_lock_key_is_stable_and_source_specific() -> None:
    """Lock identities must serialize one source without a process hash seed."""
    assert _advisory_lock_key(SOURCE_ID) == _advisory_lock_key(SOURCE_ID)
    assert _advisory_lock_key(SOURCE_ID) != _advisory_lock_key(OTHER_SOURCE_ID)
    assert -(2**63) <= _advisory_lock_key(SOURCE_ID) < 2**63


def test_try_advisory_lock_uses_transaction_scoped_postgres_function() -> None:
    """A session lock must be released by commit or rollback, not process exit."""
    session = Mock(spec=Session)
    session.scalar.return_value = True

    assert _repository(session).try_acquire_indexing_lock() is True

    statement = session.scalar.call_args.args[0]
    assert "pg_try_advisory_xact_lock" in str(statement)


def test_vector_dimension_mismatch_is_rejected_before_session_mutation() -> None:
    """A vector with the wrong shape must never reach the pgvector column."""
    session = Mock(spec=Session)

    with pytest.raises(RepositoryInvariantError, match="dimension"):
        _repository(session).save_embedding(
            CHUNK_ID,
            "a" * 64,
            (0.1, 0.2),
        )

    session.execute.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "vector",
    [
        (0.1, float("nan"), 0.3),
        (0.1, float("inf"), 0.3),
        (0.1, True, 0.3),
    ],
)
def test_non_finite_or_boolean_vector_is_rejected(vector: tuple[object, ...]) -> None:
    """Only finite real coordinates are valid reusable embeddings."""
    session = Mock(spec=Session)

    with pytest.raises(RepositoryInvariantError, match="finite real"):
        _repository(session).save_embedding(
            CHUNK_ID,
            "a" * 64,
            vector,  # type: ignore[arg-type]
        )

    session.execute.assert_not_called()


def test_tampered_index_config_hash_is_rejected_at_repository_binding() -> None:
    """A repository must reject a DB snapshot whose full hash was tampered."""
    with pytest.raises(RepositoryInvariantError, match="config_hash"):
        _repository(Mock(spec=Session), tamper_config_hash=True)


def test_descriptor_mismatch_is_rejected_at_repository_binding() -> None:
    """Runtime model identity cannot differ from the persisted document config."""
    with pytest.raises(RepositoryInvariantError, match="descriptor"):
        _repository(
            Mock(spec=Session),
            descriptor=EmbeddingDescriptor("other/model", "revision-1", 3),
        )


def test_embedding_hash_argument_mismatch_is_rejected_before_artifact_write() -> None:
    """Callers cannot select an artifact identity outside the bound IndexConfig."""
    session = Mock(spec=Session)
    repository = _repository(session)
    session.reset_mock()

    with pytest.raises(RepositoryInvariantError, match="embedding_config_hash"):
        repository.save_embedding(CHUNK_ID, "f" * 64, (0.1, 0.2, 0.3))

    session.execute.assert_not_called()
    session.add.assert_not_called()


def _parse_artifacts(*, blocks: tuple[ParsedBlock, ...] = ()) -> ParseArtifacts:
    section = ParsedSection(
        ordinal=0,
        parent_ordinal=None,
        level=1,
        heading="Title",
        heading_path=("Title",),
        body="body\n",
        line_start=1,
        line_end=2,
        blocks=blocks,
    )
    draft = ChunkDraft(
        ordinal=0,
        raw_text="body\n",
        search_text="# Title\nbody\n",
        token_count=3,
        line_start=2,
        line_end=2,
        chunk_hash="b" * 64,
    )
    return ParseArtifacts(
        parser_version="parser-v1",
        chunk_config_hash="a" * 64,
        parsed=ParsedMarkdown("parser-v1", (section,)),
        chunks=(StoredChunk(CHUNK_ID, draft),),
    )


@pytest.mark.parametrize(
    "changed_field",
    ["heading", "body", "line_end", "chunk_raw_text", "chunk_hash"],
)
def test_conflicting_persisted_parse_replay_is_rejected(
    changed_field: str,
) -> None:
    """One parse identity cannot silently accept changed persisted output."""
    repository = _repository(Mock(spec=Session))
    stored = _parse_artifacts()
    replay = _parse_artifacts()
    section = replay.parsed.sections[0]
    draft = replay.chunks[0].draft
    if changed_field in {"heading", "body", "line_end"}:
        values = {
            "ordinal": section.ordinal,
            "parent_ordinal": section.parent_ordinal,
            "level": section.level,
            "heading": "Other" if changed_field == "heading" else section.heading,
            "heading_path": (
                ("Other",) if changed_field == "heading" else section.heading_path
            ),
            "body": "changed\n" if changed_field == "body" else section.body,
            "line_start": section.line_start,
            "line_end": 3 if changed_field == "line_end" else section.line_end,
            "blocks": section.blocks,
        }
        section = ParsedSection(**values)
    else:
        draft = ChunkDraft(
            ordinal=draft.ordinal,
            raw_text=(
                "changed\n" if changed_field == "chunk_raw_text" else draft.raw_text
            ),
            search_text=draft.search_text,
            token_count=draft.token_count,
            line_start=draft.line_start,
            line_end=draft.line_end,
            chunk_hash="c" * 64 if changed_field == "chunk_hash" else draft.chunk_hash,
        )
    repository._artifacts.find_parse = Mock(return_value=stored)  # type: ignore[attr-defined,method-assign]

    with pytest.raises(RepositoryInvariantError, match="conflicting parse replay"):
        repository.save_parse(
            UUID(int=9),
            "parser-v1",
            "a" * 64,
            ParsedMarkdown("parser-v1", (section,)),
            (draft,),
        )


def test_blocks_only_parse_replay_is_allowed_by_parser_version_policy() -> None:
    """Blocks are transient; parser_version owns their deterministic behavior."""
    repository = _repository(Mock(spec=Session))
    stored = _parse_artifacts()
    block = ParsedBlock("paragraph", "body\n", 2, 2, ())
    replay = _parse_artifacts(blocks=(block,))
    repository._artifacts.find_parse = Mock(return_value=stored)  # type: ignore[attr-defined,method-assign]

    assert (
        repository.save_parse(
            UUID(int=9),
            "parser-v1",
            "a" * 64,
            replay.parsed,
            tuple(chunk.draft for chunk in replay.chunks),
        )
        == stored
    )


def test_parse_replay_requires_the_declared_parser_version() -> None:
    """A block behavior change must use a new parser-version identity."""
    repository = _repository(Mock(spec=Session))
    replay = _parse_artifacts()

    with pytest.raises(RepositoryInvariantError, match="parser identity"):
        repository.save_parse(
            UUID(int=9),
            "parser-v2",
            "a" * 64,
            replay.parsed,
            tuple(chunk.draft for chunk in replay.chunks),
        )


def test_shared_chunk_hash_rejects_a_different_float32_vector_before_write() -> None:
    """Shared document-vector identity cannot link conflicting coordinates."""
    session = Mock(spec=Session)
    repository = _repository(session)
    repository._artifacts.find_embedding = Mock(  # type: ignore[attr-defined,method-assign]
        return_value=None
    )
    repository._artifacts.find_reusable_embedding = Mock(  # type: ignore[attr-defined,method-assign]
        return_value=(0.1, 0.2, 0.3)
    )
    session.reset_mock()
    session.get.side_effect = lambda model, identifier: (
        Chunk(id=CHUNK_ID, chunk_hash="b" * 64)
        if model is Chunk and identifier == CHUNK_ID
        else None
    )

    with pytest.raises(RepositoryInvariantError, match="shared chunk hash"):
        repository.save_embedding(
            CHUNK_ID,
            _bound_embedding_hash(),
            (0.1, 0.2, 0.3001),
        )

    session.execute.assert_not_called()
    session.add.assert_not_called()


def test_relation_semantic_replay_with_changed_lines_is_rejected() -> None:
    """The same relation identity cannot accumulate conflicting evidence lines."""
    session = Mock(spec=Session)
    repository = _repository(session)
    run_id = UUID(int=40)
    first = DocumentOccurrence(
        id=UUID(int=41), run_id=run_id, source_path="docs/new.md"
    )
    second = DocumentOccurrence(
        id=UUID(int=42), run_id=run_id, source_path="docs/old.md"
    )
    session.get.side_effect = lambda model, identifier: (
        IndexRun(
            id=run_id,
            source_profile_id=SOURCE_ID,
            index_config_id=CONFIG_ID,
            status="building",
        )
        if model is IndexRun and identifier == run_id
        else None
    )
    session.scalars.return_value = (first, second)
    session.scalar.return_value = DocumentRelation(
        run_id=run_id,
        from_occurrence_id=first.id,
        to_occurrence_id=second.id,
        relation_type="supersedes",
        evidence_source_path="docs/new.md",
        evidence_line_start=1,
        evidence_line_end=1,
    )
    relation = DocumentRelationSpec(
        "docs/new.md",
        "docs/old.md",
        RelationType.SUPERSEDES,
        "docs/new.md",
        LineRange(2, 2),
    )

    with pytest.raises(RepositoryInvariantError, match="evidence line"):
        repository.save_relations(run_id, (relation,))


def test_concrete_repository_matches_task8a_protocol_signatures() -> None:
    """The PostgreSQL adapter must remain callable through the Task 8A port."""
    for method_name in (
        "create_building_run",
        "upsert_content",
        "create_occurrence",
        "find_parse",
        "save_parse",
        "find_embedding",
        "find_reusable_embedding",
        "save_embedding",
        "mark_ready",
        "mark_failed",
    ):
        protocol_method = getattr(_Repository, method_name)
        concrete_method = getattr(PostgresIndexRepository, method_name)
        assert tuple(signature(concrete_method).parameters) == tuple(
            signature(protocol_method).parameters
        )
        assert get_type_hints(concrete_method).get("return") == (
            get_type_hints(protocol_method).get("return")
        )
