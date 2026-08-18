"""Unit tests for the OMF source profile contract."""

import json
from pathlib import Path

import pytest

from omf_retrieval.application.indexing import ports
from omf_retrieval.infrastructure.source import profiles

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OMF_PROFILE_PATH = PROJECT_ROOT / "config/source_profiles/omf.json"
OMF_RELATIONS_PATH = PROJECT_ROOT / "config/source_profiles/omf-relations.json"


def test_committed_omf_profile_has_the_approved_contract() -> None:
    """The committed profile defines the approved OMF source boundaries."""
    assert OMF_PROFILE_PATH.is_file()
    assert json.loads(OMF_PROFILE_PATH.read_text(encoding="utf-8")) == {
        "source_key": "omf",
        "include_patterns": [
            "docs/research/**/*.md",
            "docs/planning/**/*.md",
            "uiux/**/*.md",
        ],
        "exclude_patterns": [
            "docs/raw/**",
            "docs/_workspace/**",
            "**/AGENTS.md",
            "**/CLAUDE.md",
            "**/.agents/**",
            "**/.claude/**",
            "**/_workspace/**",
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
    assert [
        profile.includes(source_path)
        for source_path in (
            "docs/research/a.md",
            "docs/planning/versions/v1.md",
            "uiux/spec.md",
            "docs/raw/secret.md",
            "docs/_workspace/note.md",
            "uiux/CLAUDE.md",
            "uiux/image.png",
        )
    ] == [True, True, True, False, False, False, False]


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

    assert profile.includes("docs/research/direct.md")
    assert profile.includes("docs/research/nested/deep.md")
    assert profile.includes("./uiux//spec.md")
    assert not profile.includes("docs/research/image.png")
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
    )

    assert archive_file.source_path == "docs/research/a.md"
    assert archive_file.content == b"source bytes"
    assert snapshot.archive_files == (archive_file,)
    with pytest.raises(AttributeError):
        snapshot.archive_files.append(archive_file)  # type: ignore[attr-defined]


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
        ports.SourceSnapshot(commit_sha=commit_sha, archive_files=())


def test_snapshot_rejects_duplicate_or_unsorted_archive_paths() -> None:
    """Archive files have unique lexical source-path ordering."""
    first = ports.ArchiveFile(source_path="docs/a.md", content=b"a")
    second = ports.ArchiveFile(source_path="docs/b.md", content=b"b")
    commit_sha = "0123456789abcdef0123456789abcdef01234567"

    with pytest.raises(ports.SourceSnapshotValidationError):
        ports.SourceSnapshot(commit_sha=commit_sha, archive_files=(second, first))
    with pytest.raises(ports.SourceSnapshotValidationError):
        ports.SourceSnapshot(commit_sha=commit_sha, archive_files=(first, first))


def test_snapshot_dtos_reject_mutable_or_nonbyte_inputs() -> None:
    """DTO inputs remain typed and immutable before a snapshot is exposed."""
    with pytest.raises(ports.SourceSnapshotValidationError):
        ports.ArchiveFile(source_path="docs/a.md", content="source")  # type: ignore[arg-type]
    with pytest.raises(ports.SourceSnapshotValidationError):
        ports.SourceSnapshot(
            commit_sha="0123456789abcdef0123456789abcdef01234567",
            archive_files=[],  # type: ignore[arg-type]
        )
