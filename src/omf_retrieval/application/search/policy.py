"""Immutable retrieval policy and evidence-floor helpers."""

from omf_retrieval.application.search.ports import ScoredCandidate
from omf_retrieval.settings import Settings


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
    "require_calibrated_evidence_floors",
    "retain_at_or_above",
    "retrieval_config_snapshot",
]
