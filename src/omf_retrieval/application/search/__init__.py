"""Public framework-free contracts for the MVP search vertical slice."""

from omf_retrieval.application.search.evidence import (
    EvidenceItem,
    EvidenceMatch,
    group_evidence,
)
from omf_retrieval.application.search.policy import (
    SearchPolicyManifest,
    SearchPolicyRepository,
    SearchPolicySnapshot,
    SearchPolicyValidationError,
    require_calibrated_evidence_floors,
    retain_at_or_above,
    retrieval_config_snapshot,
    validated_search_policy_snapshot,
)
from omf_retrieval.application.search.ports import (
    ActiveIndex,
    Candidate,
    CandidateBatch,
    NoActiveIndexError,
    Origin,
    ScoredCandidate,
    SearchUnavailableError,
)
from omf_retrieval.application.search.rrf import (
    FusedCandidate,
    reciprocal_rank_fusion,
)
from omf_retrieval.application.search.service import SearchResult, SearchService

__all__ = [
    "ActiveIndex",
    "Candidate",
    "CandidateBatch",
    "EvidenceItem",
    "EvidenceMatch",
    "FusedCandidate",
    "NoActiveIndexError",
    "Origin",
    "ScoredCandidate",
    "SearchResult",
    "SearchService",
    "SearchUnavailableError",
    "SearchPolicyManifest",
    "SearchPolicyRepository",
    "SearchPolicySnapshot",
    "SearchPolicyValidationError",
    "group_evidence",
    "require_calibrated_evidence_floors",
    "retain_at_or_above",
    "reciprocal_rank_fusion",
    "retrieval_config_snapshot",
    "validated_search_policy_snapshot",
]
