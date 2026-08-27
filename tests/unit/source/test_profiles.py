"""Unit tests for the OMF source profile contract."""

import json
from pathlib import Path

import pytest

from omf_retrieval.application.indexing import ports
from omf_retrieval.infrastructure.source import profiles

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OMF_PROFILE_PATH = PROJECT_ROOT / "config/source_profiles/omf.json"
OMF_RELATIONS_PATH = PROJECT_ROOT / "config/source_profiles/omf-relations.json"
VALID_COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
OMF_FIXED_COMMIT_SHA = "a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2"


class StringSubclass(str):
    """Represent an invalid string subclass at an exact-type boundary."""


class BytesSubclass(bytes):
    """Represent an invalid bytes subclass at an exact-type boundary."""


class IntegerSubclass(int):
    """Represent an invalid integer subclass at an exact-type boundary."""


class TupleSubclass(tuple[object, ...]):
    """Represent an invalid tuple subclass at an exact-type boundary."""


class ArchiveFileSubclass(ports.ArchiveFile):
    """Represent an invalid archive-file subclass in a snapshot tuple."""


def test_committed_omf_profile_has_the_approved_contract() -> None:
    """The committed profile defines the approved OMF source boundaries."""
    assert OMF_PROFILE_PATH.is_file()
    assert json.loads(OMF_PROFILE_PATH.read_text(encoding="utf-8")) == {
        "source_key": "omf",
        "commit_sha": OMF_FIXED_COMMIT_SHA,
        "include_patterns": ["design/wiki/**/*.md"],
        "exclude_patterns": [
            "design/raw/**",
            "design/schema/**",
            "docs/**",
            "**/AGENTS.md",
            "**/CLAUDE.md",
            "**/.agents/**",
            "**/.claude/**",
            "**/.codex/**",
            "**/_workspace/**",
            "**/generated/**",
            "**/temp/**",
            "**/tmp/**",
            "**/.omc/**",
            "**/crefle-doc/**",
        ],
    }


def test_committed_omf_relations_is_an_empty_future_entry_container() -> None:
    """Task 4A intentionally reserves relations schema validation for Task 5."""
    assert OMF_RELATIONS_PATH.is_file()
    assert json.loads(OMF_RELATIONS_PATH.read_text(encoding="utf-8")) == {
        "relations": []
    }


def test_omf_profile_matches_the_approved_source_matrix() -> None:
    """The profile selects only approved Markdown source paths."""
    profile_factory = getattr(profiles, "omf_profile", None)

    assert callable(profile_factory)
    profile = profile_factory()
    assert profile.commit_sha == OMF_FIXED_COMMIT_SHA
    assert [
        profile.includes(source_path)
        for source_path in (
            "design/wiki/a.md",
            "design/wiki/versions/v1.md",
            "design/raw/secret.md",
            "design/schema/model.md",
            "docs/research/legacy.md",
            "design/wiki/_workspace/note.md",
            "design/wiki/generated/output.md",
            "design/wiki/temp/note.md",
            "design/wiki/tmp/note.md",
            "design/wiki/CLAUDE.md",
            "design/wiki/.codex/work.md",
            "design/wiki/image.png",
            "design/wiki/generated.html",
        )
    ] == [
        True,
        True,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
    ]


def test_profile_applies_segment_globs_exclusions_and_canonicalization() -> None:
    """Glob boundaries include direct children and exclude protected content."""
    profile = profiles.omf_profile()
    broad_markdown_profile = profiles.SourceProfileConfig(
        source_key="test",
        include_patterns=("**/*.md",),
        exclude_patterns=(
            "**/AGENTS.md",
            "**/CLAUDE.md",
            "**/.agents/**",
            "**/.claude/**",
            "**/_workspace/**",
            "**/.omc/**",
            "**/crefle-doc/**",
        ),
    )
    exclusion_wins_profile = profiles.SourceProfileConfig(
        source_key="test",
        include_patterns=("uiux/**/*.md",),
        exclude_patterns=("uiux/private/**",),
    )
    segment_pattern_profile = profiles.SourceProfileConfig(
        source_key="test",
        include_patterns=("uiux/[sp]pec?.md",),
        exclude_patterns=(),
    )

    assert profile.includes("design/wiki/direct.md")
    assert profile.includes("design/wiki/nested/deep.md")
    assert profile.includes("./design//wiki/spec.md")
    assert not profile.includes("design/wiki/image.png")
    assert [
        broad_markdown_profile.includes(source_path)
        for source_path in (
            "AGENTS.md",
            "CLAUDE.md",
            "uiux/AGENTS.md",
            "uiux/CLAUDE.md",
            "uiux/.agents/rules.md",
            "uiux/.claude/rules.md",
            "uiux/_workspace/note.md",
            "uiux/.omc/note.md",
            "uiux/crefle-doc/note.md",
        )
    ] == [False, False, False, False, False, False, False, False, False]
    assert not exclusion_wins_profile.includes("uiux/private/spec.md")
    assert segment_pattern_profile.includes("uiux/spec1.md")
    assert not segment_pattern_profile.includes("uiux/spec.md")


def test_terminal_recursive_glob_matches_a_deep_path_without_stack_overflow() -> None:
    """A terminal ** includes arbitrarily deep repository-relative paths."""
    profile = profiles.SourceProfileConfig(
        source_key="test",
        include_patterns=("docs/**",),
        exclude_patterns=(),
    )
    deep_path = "docs/" + "/".join("section" for _ in range(1_100))

    try:
        matched = profile.includes(deep_path)
    except RecursionError:
        pytest.fail("terminal ** matching must not depend on the Python call stack")

    assert matched


def test_recursive_glob_handles_consecutive_terminal_and_deep_nonmatching_paths() -> (
    None
):
    """Segment-aware ** preserves zero-or-more semantics on adversarial paths."""
    consecutive_profile = profiles.SourceProfileConfig(
        source_key="test",
        include_patterns=("docs/**/**/guide?.md",),
        exclude_patterns=(),
    )
    terminal_profile = profiles.SourceProfileConfig(
        source_key="test",
        include_patterns=("docs/**",),
        exclude_patterns=(),
    )
    nonmatching_profile = profiles.SourceProfileConfig(
        source_key="test",
        include_patterns=("docs/**/**/**/**/**/**/**/**/target.md",),
        exclude_patterns=(),
    )
    deep_nonmatch = "docs/" + "/".join([*("section" for _ in range(18)), "other.md"])

    assert consecutive_profile.includes("docs/guide1.md")
    assert consecutive_profile.includes("docs/design/release/guide2.md")
    assert terminal_profile.includes("docs/guide.md")
    assert not nonmatching_profile.includes(deep_nonmatch)


@pytest.mark.parametrize(
    "source_path",
    ["/uiux/spec.md", "uiux/../spec.md", "uiux\\spec.md", "uiux/\x00spec.md", "", "."],
)
def test_profile_rejects_unsafe_or_empty_source_paths(source_path: str) -> None:
    """Unsafe non-POSIX paths cannot be selected from a source profile."""
    with pytest.raises(profiles.SourceProfileValidationError):
        profiles.omf_profile().includes(source_path)


def test_profile_config_rejects_mutable_pattern_containers() -> None:
    """Mutable inputs cannot undermine an immutable profile contract."""
    with pytest.raises(profiles.SourceProfileValidationError):
        profiles.SourceProfileConfig(
            source_key="test",
            include_patterns=["uiux/**/*.md"],  # type: ignore[arg-type]
            exclude_patterns=(),
        )


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: profiles.SourceProfileConfig(
            source_key=StringSubclass("test"),
            include_patterns=("uiux/**/*.md",),
            exclude_patterns=(),
        ),
        lambda: profiles.SourceProfileConfig(
            source_key="test",
            include_patterns=(StringSubclass("uiux/**/*.md"),),
            exclude_patterns=(),
        ),
        lambda: profiles.SourceProfileConfig(
            source_key="test",
            include_patterns=TupleSubclass(("uiux/**/*.md",)),
            exclude_patterns=(),
        ),
        lambda: ports.ArchiveFile(
            source_path=StringSubclass("docs/a.md"), content=b"source"
        ),
        lambda: ports.ArchiveFile(
            source_path="docs/a.md", content=BytesSubclass(b"source")
        ),
        lambda: ports.SourceSnapshot(
            commit_sha=StringSubclass(VALID_COMMIT_SHA),
            archive_files=(),
            excluded_file_count=0,
        ),
        lambda: ports.SourceSnapshot(
            commit_sha=VALID_COMMIT_SHA,
            archive_files=TupleSubclass(()),
            excluded_file_count=0,
        ),
    ],
)
def test_public_contracts_reject_builtin_subclasses(constructor: object) -> None:
    """Public value objects reject subclasses at exact runtime type boundaries."""
    with pytest.raises(
        (
            profiles.SourceProfileValidationError,
            ports.SourceSnapshotValidationError,
        )
    ):
        constructor()  # type: ignore[operator]


def test_canonical_source_path_rejects_a_string_subclass() -> None:
    """The standalone path canonicalizer enforces the same exact string boundary."""
    with pytest.raises(profiles.SourceProfileValidationError):
        profiles.canonical_source_path(StringSubclass("docs/a.md"))


def test_snapshot_rejects_an_archive_file_subclass() -> None:
    """Archive-file entries use the same exact runtime type contract."""
    archive_file = ArchiveFileSubclass(source_path="docs/a.md", content=b"source")

    with pytest.raises(ports.SourceSnapshotValidationError):
        ports.SourceSnapshot(
            commit_sha=VALID_COMMIT_SHA,
            archive_files=(archive_file,),
            excluded_file_count=0,
        )


def test_loader_returns_immutable_profile_tuples(tmp_path: Path) -> None:
    """A valid JSON profile is loaded into immutable pattern tuples."""
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "source_key": "sample",
                "include_patterns": ["docs/**/*.md"],
                "exclude_patterns": ["docs/raw/**"],
            }
        ),
        encoding="utf-8",
    )

    profile = profiles.load_source_profile(profile_path)

    assert profile.source_key == "sample"
    assert profile.commit_sha is None
    assert profile.include_patterns == ("docs/**/*.md",)
    assert profile.exclude_patterns == ("docs/raw/**",)
    with pytest.raises(AttributeError):
        profile.include_patterns.append("uiux/**/*.md")  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "raw_profile",
    [
        "{",
        "[]",
        '{"source_key": "sample", "include_patterns": []}',
        '{"source_key": "sample", "include_patterns": [], "exclude_patterns": [], "extra": true}',
        '{"source_key": "sample", "commit_sha": "not-a-commit", "include_patterns": [], "exclude_patterns": []}',
        '{"source_key": "sample", "include_patterns": [], "exclude_patterns": "docs/raw/**"}',
        '{"source_key": "", "include_patterns": [], "exclude_patterns": []}',
        '{"source_key": "sample", "include_patterns": [""], "exclude_patterns": []}',
    ],
)
def test_loader_rejects_malformed_or_invalid_profile_contract(
    tmp_path: Path, raw_profile: str
) -> None:
    """Malformed JSON and invalid profile shapes fail closed."""
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(raw_profile, encoding="utf-8")

    with pytest.raises(profiles.SourceProfileValidationError):
        profiles.load_source_profile(profile_path)


def test_snapshot_dtos_retain_immutable_sorted_source_bytes() -> None:
    """Snapshots retain canonical archive bytes in lexical source-path order."""
    archive_file_type = getattr(ports, "ArchiveFile", None)

    assert callable(archive_file_type)
    archive_file = archive_file_type(
        source_path="docs/research/a.md", content=b"source bytes"
    )
    snapshot = ports.SourceSnapshot(
        commit_sha="0123456789abcdef0123456789abcdef01234567",
        archive_files=(archive_file,),
        excluded_file_count=3,
    )

    assert archive_file.source_path == "docs/research/a.md"
    assert archive_file.content == b"source bytes"
    assert snapshot.archive_files == (archive_file,)
    assert snapshot.excluded_file_count == 3
    with pytest.raises(AttributeError):
        snapshot.archive_files.append(archive_file)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "excluded_file_count",
    [-1, True, 1.0, "1", None, IntegerSubclass(1)],
)
def test_snapshot_rejects_invalid_excluded_file_count(
    excluded_file_count: object,
) -> None:
    """Excluded-file totals are non-negative exact integers."""
    with pytest.raises(ports.SourceSnapshotValidationError):
        ports.SourceSnapshot(
            commit_sha=VALID_COMMIT_SHA,
            archive_files=(),
            excluded_file_count=excluded_file_count,  # type: ignore[arg-type]
        )


def test_snapshot_accepts_zero_excluded_files() -> None:
    """A provider must explicitly report a valid zero exclusion total."""
    snapshot = ports.SourceSnapshot(
        commit_sha=VALID_COMMIT_SHA,
        archive_files=(),
        excluded_file_count=0,
    )

    assert snapshot.excluded_file_count == 0


@pytest.mark.parametrize(
    "source_path",
    [
        "",
        ".",
        "./docs/a.md",
        "docs//a.md",
        "/docs/a.md",
        "docs/../a.md",
        "docs\\a.md",
        "docs/\x00a.md",
    ],
)
def test_archive_file_rejects_noncanonical_or_unsafe_source_paths(
    source_path: str,
) -> None:
    """Archive DTO paths are already canonical repository coordinates."""
    with pytest.raises(ports.SourceSnapshotValidationError):
        ports.ArchiveFile(source_path=source_path, content=b"source")


@pytest.mark.parametrize(
    "commit_sha",
    [
        "0123456789abcdef0123456789abcdef0123456",
        "0123456789abcdef0123456789abcdef012345678",
        "0123456789abcdef0123456789abcdef0123456g",
        "0123456789ABCDEF0123456789ABCDEF01234567",
    ],
)
def test_snapshot_rejects_noncanonical_git_sha(commit_sha: str) -> None:
    """Snapshot commits are lowercase, full Git SHA-1 identifiers."""
    with pytest.raises(ports.SourceSnapshotValidationError):
        ports.SourceSnapshot(
            commit_sha=commit_sha,
            archive_files=(),
            excluded_file_count=0,
        )


def test_snapshot_rejects_duplicate_or_unsorted_archive_paths() -> None:
    """Archive files have unique lexical source-path ordering."""
    first = ports.ArchiveFile(source_path="docs/a.md", content=b"a")
    second = ports.ArchiveFile(source_path="docs/b.md", content=b"b")
    commit_sha = "0123456789abcdef0123456789abcdef01234567"

    with pytest.raises(ports.SourceSnapshotValidationError):
        ports.SourceSnapshot(
            commit_sha=commit_sha,
            archive_files=(second, first),
            excluded_file_count=0,
        )
    with pytest.raises(ports.SourceSnapshotValidationError):
        ports.SourceSnapshot(
            commit_sha=commit_sha,
            archive_files=(first, first),
            excluded_file_count=0,
        )


def test_snapshot_dtos_reject_mutable_or_nonbyte_inputs() -> None:
    """DTO inputs remain typed and immutable before a snapshot is exposed."""
    with pytest.raises(ports.SourceSnapshotValidationError):
        ports.ArchiveFile(source_path="docs/a.md", content="source")  # type: ignore[arg-type]
    with pytest.raises(ports.SourceSnapshotValidationError):
        ports.SourceSnapshot(
            commit_sha="0123456789abcdef0123456789abcdef01234567",
            archive_files=[],  # type: ignore[arg-type]
            excluded_file_count=0,
        )
