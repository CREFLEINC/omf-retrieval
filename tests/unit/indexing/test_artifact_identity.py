"""Unit tests for canonical persisted parse artifact manifests."""

from dataclasses import replace
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID

import pytest

from omf_retrieval.application.indexing import artifact_identity
from omf_retrieval.application.indexing.ports import (
    ChunkDraft,
    ChunkWarning,
    ParsedBlock,
    ParsedSection,
)


def _sections(*, blocks: tuple[ParsedBlock, ...] = ()) -> tuple[ParsedSection, ...]:
    return (
        ParsedSection(
            ordinal=0,
            parent_ordinal=None,
            level=1,
            heading="A",
            heading_path=("A",),
            body="first\n",
            line_start=1,
            line_end=2,
            blocks=blocks,
        ),
        ParsedSection(
            ordinal=1,
            parent_ordinal=0,
            level=2,
            heading="B",
            heading_path=("A", "B"),
            body="second\n",
            line_start=3,
            line_end=4,
            blocks=(),
        ),
    )


def _chunks() -> tuple[ChunkDraft, ...]:
    return (
        ChunkDraft(0, "first\n", "A\nfirst\n", 3, 2, 2, "a" * 64),
        ChunkDraft(0, "second\n", "A\nB\nsecond\n", 4, 4, 4, "b" * 64),
    )


def _manifest(
    sections: tuple[ParsedSection, ...] | None = None,
    chunks: tuple[ChunkDraft, ...] | None = None,
    owners: tuple[int, ...] = (0, 1),
) -> object:
    manifest = getattr(artifact_identity, "parse_artifact_manifest", None)
    assert manifest is not None
    return manifest(
        _sections() if sections is None else sections,
        _chunks() if chunks is None else chunks,
        owners,
    )


def test_manifest_is_stable_id_free_and_block_insensitive() -> None:
    block = ParsedBlock("paragraph", "first\n", 2, 2, ())
    warning = ChunkWarning("table", 4, 4)
    warned_chunks = (_chunks()[0], replace(_chunks()[1], warnings=(warning,)))

    first = _manifest()
    repeated = _manifest(_sections(blocks=(block,)))
    warned = _manifest(chunks=warned_chunks)

    assert first == repeated == warned
    assert first.section_count == 2
    assert first.chunk_count == 2
    assert len(first.artifact_hash) == 64


@pytest.mark.parametrize(
    "mutate",
    [
        lambda sections: (sections[0], replace(sections[1], parent_ordinal=None)),
        lambda sections: (sections[0], replace(sections[1], level=3)),
        lambda sections: (
            sections[0],
            replace(sections[1], heading="C", heading_path=("A", "C")),
        ),
        lambda sections: (
            sections[0],
            replace(sections[1], heading_path=("X", "B")),
        ),
        lambda sections: (sections[0], replace(sections[1], body="changed\n")),
        lambda sections: (sections[0], replace(sections[1], line_start=2)),
        lambda sections: (sections[0], replace(sections[1], line_end=5)),
    ],
)
def test_every_mutable_persisted_section_field_changes_hash(mutate: object) -> None:
    original = _sections()
    changed = mutate(original)  # type: ignore[operator]

    assert _manifest(changed).artifact_hash != _manifest(original).artifact_hash


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("raw_text", "changed\n"),
        ("search_text", "changed search"),
        ("token_count", 9),
        ("line_start", 3),
        ("line_end", 5),
        ("chunk_hash", "c" * 64),
    ],
)
def test_every_mutable_persisted_chunk_field_changes_hash(
    field: str,
    value: object,
) -> None:
    chunks = _chunks()
    changed = (chunks[0], replace(chunks[1], **{field: value}))

    assert _manifest(chunks=changed).artifact_hash != _manifest().artifact_hash


def test_manifest_rejects_invalid_section_chunk_order_and_zero_section_parse() -> None:
    manifest = getattr(artifact_identity, "parse_artifact_manifest", None)
    assert manifest is not None

    with pytest.raises(ValueError, match="section order"):
        manifest(tuple(reversed(_sections())), _chunks(), (0, 1))
    with pytest.raises(ValueError, match="chunk order"):
        manifest(_sections(), tuple(reversed(_chunks())), (1, 0))
    with pytest.raises(ValueError, match="section order"):
        manifest(
            (_sections()[0], replace(_sections()[1], ordinal=2)),
            _chunks(),
            (0, 1),
        )
    with pytest.raises(ValueError, match="chunk order"):
        manifest(
            _sections(),
            (_chunks()[0], replace(_chunks()[1], ordinal=1)),
            (0, 1),
        )
    with pytest.raises(ValueError, match="chunk order"):
        manifest(_sections(), _chunks(), (1, 0))
    with pytest.raises(ValueError, match="at least one section"):
        manifest((), (), ())


def test_heading_only_zero_chunks_valid_but_searchable_section_requires_chunk() -> None:
    heading_only = replace(_sections()[0], body="", line_end=1)
    searchable = _sections()[0]

    manifest = _manifest((heading_only,), (), ())

    assert (manifest.section_count, manifest.chunk_count) == (1, 0)
    with pytest.raises(ValueError, match="searchable section"):
        _manifest((searchable,), (), ())


def test_migration_frozen_backfill_hash_matches_application_identity() -> None:
    migration_path = (
        Path(__file__).parents[3]
        / "migrations/versions/0002_index_run_activation_lifecycle.py"
    )
    spec = spec_from_file_location("task9_artifact_manifest_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    frozen = getattr(migration, "_frozen_artifact_manifest", None)
    sections = _sections()
    chunks = _chunks()

    assert frozen is not None
    frozen_manifest = frozen(
        tuple(
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
        ),
        tuple(
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
            for owner, chunk in zip((0, 1), chunks, strict=True)
        ),
    )

    assert frozen_manifest == (
        _manifest().section_count,
        _manifest().chunk_count,
        _manifest().artifact_hash,
    )


class _MigrationResult:
    def __init__(self, rows: tuple[object, ...]) -> None:
        self._rows = rows

    def scalars(self) -> tuple[object, ...]:
        return self._rows

    def mappings(self) -> tuple[object, ...]:
        return self._rows


class _BackfillRecordingConnection:
    def __init__(
        self,
        parse_count: int = 0,
        *,
        parse_ids: tuple[UUID, ...] | None = None,
    ) -> None:
        self.parse_ids = (
            tuple(UUID(int=ordinal + 1) for ordinal in range(parse_count))
            if parse_ids is None
            else parse_ids
        )
        self.calls: list[tuple[str, object]] = []

    def execute(self, statement: object, parameters: object = None) -> _MigrationResult:
        sql = " ".join(str(statement).split()).lower()
        self.calls.append((sql, parameters))
        if sql.startswith("select id from document_parses"):
            assert isinstance(parameters, dict)
            after_id = parameters.get("after_id")
            batch_size = parameters["batch_size"]
            return _MigrationResult(
                tuple(
                    parse_id
                    for parse_id in self.parse_ids
                    if after_id is None or parse_id > after_id
                )[:batch_size]
            )
        if "from sections as section" in sql:
            assert isinstance(parameters, dict)
            return _MigrationResult(
                tuple(
                    {
                        "parse_id": parse_id,
                        "ordinal": 0,
                        "parent_ordinal": None,
                        "level": 1,
                        "heading": "H",
                        "heading_path": ["H"],
                        "body": "",
                        "line_start": 1,
                        "line_end": 1,
                    }
                    for parse_id in parameters["parse_ids"]
                )
            )
        if "from chunks as chunk" in sql:
            return _MigrationResult(())
        if sql.startswith("update document_parses"):
            assert isinstance(parameters, list)
            return _MigrationResult(())
        raise AssertionError(sql)


@pytest.mark.parametrize(
    ("case_name", "batch_count"),
    [("one", 1), ("full", 1), ("plus-one", 2), ("many", 4)],
)
def test_migration_manifest_backfill_is_keyset_batched_without_n_plus_one(
    case_name: str,
    batch_count: int,
) -> None:
    migration_path = (
        Path(__file__).parents[3]
        / "migrations/versions/0002_index_run_activation_lifecycle.py"
    )
    spec = spec_from_file_location(
        f"task9_artifact_batch_migration_{case_name}", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    batch_size = getattr(migration, "_PARSE_BACKFILL_BATCH_SIZE", None)
    backfill = getattr(migration, "_backfill_parse_artifact_manifests", None)
    assert type(batch_size) is int and batch_size > 1
    parse_count = {
        "one": 1,
        "full": batch_size,
        "plus-one": batch_size + 1,
        "many": batch_size * 3 + 1,
    }[case_name]
    connection = _BackfillRecordingConnection(parse_count)

    assert backfill is not None
    backfill(connection)

    id_selects = [call for call in connection.calls if call[0].startswith("select id")]
    section_selects = [
        call for call in connection.calls if "from sections as section" in call[0]
    ]
    chunk_selects = [
        call for call in connection.calls if "from chunks as chunk" in call[0]
    ]
    updates = [
        call
        for call in connection.calls
        if call[0].startswith("update document_parses")
    ]
    assert len(id_selects) == batch_count + 1
    assert len(section_selects) == len(chunk_selects) == len(updates) == batch_count
    assert len(connection.calls) == 4 * batch_count + 1
    assert all("offset" not in sql for sql, _ in connection.calls)
    assert [call[1].get("after_id") for call in id_selects] == [
        None,
        *(
            connection.parse_ids[min(index * batch_size, parse_count) - 1]
            for index in range(1, batch_count + 1)
        ),
    ]
    assert "where id >" not in id_selects[0][0]
    assert all("where id >" in sql for sql, _ in id_selects[1:])
    assert all(len(call[1]["parse_ids"]) <= batch_size for call in section_selects)
    assert all(len(call[1]) <= batch_size for call in updates)
    assert (
        tuple(
            parameters["parse_id"]
            for _, batch_parameters in updates
            for parameters in batch_parameters
        )
        == connection.parse_ids
    )


def test_migration_keyset_backfill_includes_zero_boundary_and_max_uuid_once() -> None:
    migration_path = (
        Path(__file__).parents[3]
        / "migrations/versions/0002_index_run_activation_lifecycle.py"
    )
    spec = spec_from_file_location("task9_artifact_uuid_edges", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    batch_size = migration._PARSE_BACKFILL_BATCH_SIZE
    parse_ids = (
        *(UUID(int=ordinal) for ordinal in range(batch_size + 1)),
        UUID(int=(1 << 128) - 1),
    )
    connection = _BackfillRecordingConnection(parse_ids=parse_ids)

    migration._backfill_parse_artifact_manifests(connection)

    updates = [
        parameters
        for sql, parameters in connection.calls
        if sql.startswith("update document_parses")
    ]
    updated_ids = tuple(
        parameters["parse_id"]
        for batch_parameters in updates
        for parameters in batch_parameters
    )
    assert updated_ids == parse_ids
    assert len(updated_ids) == len(set(updated_ids))
