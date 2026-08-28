"""OMF source-profile configuration and path-selection helpers."""

import json
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path


class SourceProfileValidationError(ValueError):
    """Raised when a source-profile contract is invalid."""


@dataclass(frozen=True, slots=True)
class SourceProfileConfig:
    """Represent immutable source-selection rules for one repository.

    Args:
        source_key: Non-empty profile identifier.
        include_patterns: Repository-relative POSIX glob patterns to include.
        exclude_patterns: Repository-relative POSIX glob patterns to exclude.
        commit_sha: Optional fixed lowercase full Git commit for this profile.
    """

    source_key: str
    include_patterns: tuple[str, ...]
    exclude_patterns: tuple[str, ...]
    commit_sha: str | None = None

    def __post_init__(self) -> None:
        """Validate profile fields before exposing the immutable profile."""
        _require_non_empty_string(value=self.source_key, field_name="source_key")
        _require_pattern_tuple(
            value=self.include_patterns, field_name="include_patterns"
        )
        _require_pattern_tuple(
            value=self.exclude_patterns, field_name="exclude_patterns"
        )
        _require_patterns(value=self.include_patterns, field_name="include_patterns")
        _require_patterns(value=self.exclude_patterns, field_name="exclude_patterns")
        if self.commit_sha is not None and (
            type(self.commit_sha) is not str
            or re.fullmatch(r"[0-9a-f]{40}", self.commit_sha) is None
        ):
            raise SourceProfileValidationError(
                "commit_sha must be a lowercase full Git SHA"
            )

    def includes(self, source_path: str) -> bool:
        """Return whether a canonicalized repository path is selectable.

        Args:
            source_path: POSIX repository-relative source path.

        Raises:
            SourceProfileValidationError: If the path is unsafe or not canonicalizable.
        """
        canonical_path = canonical_source_path(source_path)
        return any(
            _matches_path(pattern=pattern, source_path=canonical_path)
            for pattern in self.include_patterns
        ) and not any(
            _matches_path(pattern=pattern, source_path=canonical_path)
            for pattern in self.exclude_patterns
        )


def omf_profile() -> SourceProfileConfig:
    """Load the committed OMF source profile."""
    return load_source_profile(_project_root() / "config/source_profiles/omf.json")


def load_source_profile(profile_path: Path) -> SourceProfileConfig:
    """Load and validate an immutable source profile from a JSON object.

    Args:
        profile_path: JSON profile file to validate.

    Raises:
        SourceProfileValidationError: If JSON or the profile contract is invalid.
    """
    try:
        raw_profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceProfileValidationError(
            f"Cannot load source profile: {profile_path}"
        ) from error

    if type(raw_profile) is not dict:
        raise SourceProfileValidationError("Source profile must be a JSON object")

    required_keys = {"source_key", "include_patterns", "exclude_patterns"}
    if set(raw_profile) not in (required_keys, required_keys | {"commit_sha"}):
        raise SourceProfileValidationError(
            "Source profile keys must match the contract"
        )

    source_key = raw_profile["source_key"]
    include_patterns = raw_profile["include_patterns"]
    exclude_patterns = raw_profile["exclude_patterns"]
    _require_non_empty_string(value=source_key, field_name="source_key")
    _require_pattern_list(value=include_patterns, field_name="include_patterns")
    _require_pattern_list(value=exclude_patterns, field_name="exclude_patterns")

    return SourceProfileConfig(
        source_key=source_key,
        include_patterns=tuple(include_patterns),
        exclude_patterns=tuple(exclude_patterns),
        commit_sha=raw_profile.get("commit_sha"),
    )


def canonical_source_path(source_path: str) -> str:
    """Return a safe canonical POSIX repository-relative path.

    Args:
        source_path: Candidate repository-relative POSIX path.

    Raises:
        SourceProfileValidationError: If the path is absolute or unsafe.
    """
    if type(source_path) is not str:
        raise SourceProfileValidationError("Source path must be a string")
    if not source_path or "\x00" in source_path or "\\" in source_path:
        raise SourceProfileValidationError("Source path must be a non-empty POSIX path")
    if source_path.startswith("/"):
        raise SourceProfileValidationError("Source path must be repository-relative")

    parts = [part for part in source_path.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        raise SourceProfileValidationError(
            "Source path must not traverse parent directories"
        )
    return "/".join(parts)


def _project_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        profile_path = candidate / "config/source_profiles/omf.json"
        if (candidate / "pyproject.toml").is_file() and profile_path.is_file():
            return candidate

    raise SourceProfileValidationError("OMF source profile root is unavailable")


def _require_non_empty_string(*, value: object, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise SourceProfileValidationError(f"{field_name} must be a non-empty string")


def _require_pattern_list(*, value: object, field_name: str) -> None:
    if type(value) is not list:
        raise SourceProfileValidationError(f"{field_name} must be a JSON array")
    _require_patterns(value=tuple(value), field_name=field_name)


def _require_pattern_tuple(*, value: object, field_name: str) -> None:
    if type(value) is not tuple:
        raise SourceProfileValidationError(f"{field_name} must be an immutable tuple")


def _require_patterns(*, value: tuple[str, ...], field_name: str) -> None:
    if any(type(pattern) is not str or not pattern.strip() for pattern in value):
        raise SourceProfileValidationError(
            f"{field_name} must contain only non-empty strings"
        )


def _matches_path(*, pattern: str, source_path: str) -> bool:
    return _match_segments(
        pattern_segments=pattern.split("/"),
        path_segments=source_path.split("/"),
    )


def _match_segments(
    *,
    pattern_segments: list[str],
    path_segments: list[str],
    pattern_index: int = 0,
    path_index: int = 0,
) -> bool:
    pending_states = [(pattern_index, path_index)]
    visited_states = set(pending_states)
    pattern_length = len(pattern_segments)
    path_length = len(path_segments)

    while pending_states:
        current_pattern_index, current_path_index = pending_states.pop()
        if current_pattern_index == pattern_length:
            if current_path_index == path_length:
                return True
            continue

        pattern_segment = pattern_segments[current_pattern_index]
        if pattern_segment == "**":
            zero_segment_state = (current_pattern_index + 1, current_path_index)
            if zero_segment_state not in visited_states:
                visited_states.add(zero_segment_state)
                pending_states.append(zero_segment_state)

            if current_path_index < path_length:
                consuming_state = (current_pattern_index, current_path_index + 1)
                if consuming_state not in visited_states:
                    visited_states.add(consuming_state)
                    pending_states.append(consuming_state)
            continue

        if current_path_index < path_length and fnmatchcase(
            path_segments[current_path_index], pattern_segment
        ):
            next_state = (current_pattern_index + 1, current_path_index + 1)
            if next_state not in visited_states:
                visited_states.add(next_state)
                pending_states.append(next_state)

    return False
