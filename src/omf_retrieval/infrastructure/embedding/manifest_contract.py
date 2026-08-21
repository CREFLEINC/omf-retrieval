"""Shared schema and path contracts for embedding-model manifests."""

from __future__ import annotations

import unicodedata
from pathlib import PurePosixPath

MODEL_MANIFEST_SCHEMA = "omf-retrieval.embedding-model-manifest"
MODEL_MANIFEST_VERSION = 1
MAX_MODEL_FILE_COUNT = 256
MAX_MODEL_DIRECTORY_COUNT = MAX_MODEL_FILE_COUNT
MAX_MODEL_FILE_BYTES = 4 * 1024**3
MAX_MODEL_TOTAL_BYTES = 8 * 1024**3
MAX_MODEL_MANIFEST_BYTES = MAX_MODEL_FILE_COUNT * (4096 + 512)
APPROVED_MODEL_FILE_SUFFIXES = (".json", ".model", ".safetensors", ".txt")
MODEL_MANIFEST_RELATIVE_PATH = PurePosixPath(
    ".omf-retrieval/embedding-model-manifest.json"
)
HEX_DIGITS = frozenset("0123456789abcdef")


class ModelManifestError(ValueError):
    """Report a source-free model-manifest validation failure."""


def validated_relative_path(value: object) -> str:
    """Return one canonical safe POSIX path or reject it source-free."""
    if (
        type(value) is not str
        or not value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ModelManifestError("Embedding model manifest is invalid")
    if "\\" in value or any(
        unicodedata.category(character) in {"Cc", "Cf"} for character in value
    ):
        raise ModelManifestError("Embedding model manifest is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ModelManifestError("Embedding model manifest is invalid")
    return value


def validated_snapshot_coordinate(value: object) -> str:
    """Return the fixed private snapshot coordinate shape."""
    coordinate = validated_relative_path(value)
    parts = PurePosixPath(coordinate).parts
    if (
        len(parts) != 3
        or parts[:2] != (".omf-retrieval", "snapshots")
        or not is_sha256(parts[2])
    ):
        raise ModelManifestError("Embedding model manifest is invalid")
    return coordinate


def require_exact_identity(model_name: object, revision: object) -> None:
    """Reject missing or non-string model identity fields."""
    if (
        type(model_name) is not str
        or not model_name.strip()
        or type(revision) is not str
        or not revision.strip()
    ):
        raise ModelManifestError("Embedding model manifest is invalid")


def is_sha256(value: object) -> bool:
    """Return whether a value is one lowercase SHA-256 hex digest."""
    return type(value) is str and len(value) == 64 and set(value) <= HEX_DIGITS


def is_approved_model_path(path: str) -> bool:
    """Return whether the model path has an approved data-file suffix."""
    return path.endswith(APPROVED_MODEL_FILE_SUFFIXES)
