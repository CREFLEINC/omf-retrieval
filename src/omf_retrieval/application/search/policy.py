"""Immutable retrieval policy identities and evidence-floor helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import isfinite
from typing import Protocol
from uuid import UUID

from omf_retrieval.application.indexing.hashing import canonical_json, config_hash
from omf_retrieval.application.search.ports import ScoredCandidate
from omf_retrieval.settings import Settings

_POLICY_KEYS = {
    "query_embedding_model_name",
    "query_embedding_revision",
    "query_embedding_dimension",
    "query_embedding_normalize_embeddings",
    "query_instruction",
    "keyword_candidate_limit",
    "vector_candidate_limit",
    "rrf_k",
    "keyword_weight",
    "vector_weight",
    "keyword_similarity_floor",
    "vector_similarity_floor",
    "calibration_status",
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_VALID_CALIBRATION_STATES = {"calibration_pending", "calibrated"}


class SearchPolicyValidationError(ValueError):
    """Report an invalid policy without reflecting unsafe input values."""


@dataclass(frozen=True, slots=True)
class SearchPolicySnapshot:
    """One exact, immutable query-time search policy snapshot."""

    query_embedding_model_name: str
    query_embedding_revision: str
    query_embedding_dimension: int
    query_embedding_normalize_embeddings: bool
    query_instruction: str
    keyword_candidate_limit: int
    vector_candidate_limit: int
    rrf_k: int
    keyword_weight: float
    vector_weight: float
    keyword_similarity_floor: float
    vector_similarity_floor: float
    calibration_status: str

    def __post_init__(self) -> None:
        """Apply the exact contract to every construction path."""
        for field_name in (
            "keyword_similarity_floor",
            "vector_similarity_floor",
        ):
            value = getattr(self, field_name)
            if type(value) is float and value == 0.0:
                object.__setattr__(self, field_name, 0.0)
        _validate_search_policy_config(self.as_config())

    def as_config(self) -> dict[str, object]:
        """Return the exact JSON projection used for persistence and hashing."""
        return {
            "query_embedding_model_name": self.query_embedding_model_name,
            "query_embedding_revision": self.query_embedding_revision,
            "query_embedding_dimension": self.query_embedding_dimension,
            "query_embedding_normalize_embeddings": (
                self.query_embedding_normalize_embeddings
            ),
            "query_instruction": self.query_instruction,
            "keyword_candidate_limit": self.keyword_candidate_limit,
            "vector_candidate_limit": self.vector_candidate_limit,
            "rrf_k": self.rrf_k,
            "keyword_weight": self.keyword_weight,
            "vector_weight": self.vector_weight,
            "keyword_similarity_floor": self.keyword_similarity_floor,
            "vector_similarity_floor": self.vector_similarity_floor,
            "calibration_status": self.calibration_status,
        }

    @property
    def canonical_json(self) -> bytes:
        """Return stable UTF-8 canonical JSON bytes."""
        return canonical_json(self.as_config())

    @property
    def config_hash(self) -> str:
        """Return the canonical SHA-256 search policy identity."""
        return config_hash(self.as_config())


@dataclass(frozen=True, slots=True)
class SearchPolicyManifest:
    """One persisted policy coordinate bound to its canonical snapshot."""

    policy_id: UUID
    config_hash: str
    snapshot: SearchPolicySnapshot

    def __post_init__(self) -> None:
        if (
            type(self.policy_id) is not UUID
            or type(self.config_hash) is not str
            or _SHA256.fullmatch(self.config_hash) is None
            or type(self.snapshot) is not SearchPolicySnapshot
            or self.config_hash != self.snapshot.config_hash
        ):
            raise SearchPolicyValidationError("Search policy manifest is invalid")


class SearchPolicyRepository(Protocol):
    """Append-only persistence operations for policy manifests."""

    def register(self, snapshot: SearchPolicySnapshot) -> SearchPolicyManifest:
        """Idempotently persist and return one canonical snapshot."""

    def resolve(self, config_hash: str) -> SearchPolicyManifest:
        """Resolve and revalidate one canonical manifest by hash."""


def validated_search_policy_snapshot(value: object) -> SearchPolicySnapshot:
    """Validate an exact JSON object and return its immutable representation."""
    _validate_search_policy_config(value)
    return SearchPolicySnapshot(**value)  # type: ignore[arg-type]


def _validate_search_policy_config(value: object) -> None:
    if type(value) is not dict or set(value) != _POLICY_KEYS:
        raise SearchPolicyValidationError("Search policy snapshot is invalid")
    strings = (
        "query_embedding_model_name",
        "query_embedding_revision",
        "query_instruction",
    )
    integers = (
        "query_embedding_dimension",
        "keyword_candidate_limit",
        "vector_candidate_limit",
        "rrf_k",
    )
    positive_floats = ("keyword_weight", "vector_weight")
    floors = ("keyword_similarity_floor", "vector_similarity_floor")
    if any(type(value[key]) is not str or not value[key].strip() for key in strings):
        raise SearchPolicyValidationError("Search policy snapshot is invalid")
    if any(type(value[key]) is not int or value[key] <= 0 for key in integers):
        raise SearchPolicyValidationError("Search policy snapshot is invalid")
    if type(value["query_embedding_normalize_embeddings"]) is not bool:
        raise SearchPolicyValidationError("Search policy snapshot is invalid")
    if any(
        type(value[key]) is not float or not isfinite(value[key]) or value[key] <= 0.0
        for key in positive_floats
    ):
        raise SearchPolicyValidationError("Search policy snapshot is invalid")
    if any(
        type(value[key]) is not float
        or not isfinite(value[key])
        or not 0.0 <= value[key] <= 1.0
        for key in floors
    ):
        raise SearchPolicyValidationError("Search policy snapshot is invalid")
    if (
        type(value["calibration_status"]) is not str
        or value["calibration_status"] not in _VALID_CALIBRATION_STATES
    ):
        raise SearchPolicyValidationError("Search policy snapshot is invalid")


def retrieval_config_snapshot(settings: Settings) -> dict[str, object]:
    """Build the exact JSON identity persisted with an index generation."""
    if type(settings) is not Settings:
        raise TypeError("settings must use the exact Settings contract")
    return {
        "k": settings.rrf_k,
        "keyword_weight": settings.keyword_weight,
        "vector_weight": settings.vector_weight,
        "keyword_similarity_floor": settings.keyword_similarity_floor,
        "vector_similarity_floor": settings.vector_similarity_floor,
        "evidence_floor_status": settings.evidence_floor_status,
    }


def require_calibrated_evidence_floors(settings: Settings) -> None:
    """Fail closed until an independently calibrated policy is explicit."""
    if type(settings) is not Settings or settings.evidence_floor_status != "calibrated":
        raise ValueError("Evidence floors are not calibrated")


def retain_at_or_above(
    candidates: tuple[ScoredCandidate, ...], floor: float
) -> tuple[ScoredCandidate, ...]:
    """Apply the approved inclusive floor while preserving lane rank order."""
    if type(candidates) is not tuple or type(floor) is not float:
        raise ValueError("Invalid evidence-floor input")
    return tuple(candidate for candidate in candidates if candidate.raw_score >= floor)


__all__ = [
    "SearchPolicyManifest",
    "SearchPolicyRepository",
    "SearchPolicySnapshot",
    "SearchPolicyValidationError",
    "require_calibrated_evidence_floors",
    "retain_at_or_above",
    "retrieval_config_snapshot",
    "validated_search_policy_snapshot",
]
