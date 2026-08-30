"""Authenticated hybrid-search application service."""

from dataclasses import dataclass

from omf_retrieval.application.admin.tokens import AuthorizedSource, SourceAccessError
from omf_retrieval.application.search.evidence import EvidenceItem, group_evidence
from omf_retrieval.application.search.policy import (
    SearchPolicyManifest,
    SearchPolicySnapshot,
    retain_at_or_above,
)
from omf_retrieval.application.search.ports import (
    ActiveIndex,
    NoActiveIndexError,
    QueryEmbeddingProvider,
    SearchRepository,
    SearchUnavailableError,
)
from omf_retrieval.application.search.rrf import reciprocal_rank_fusion
from omf_retrieval.settings import Settings


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Complete API-neutral result for one active source generation."""

    status: str
    index: ActiveIndex
    search_policy: SearchPolicyManifest
    evidence_items: tuple[EvidenceItem, ...]

    def __post_init__(self) -> None:
        if self.status not in {"ok", "no_evidence"}:
            raise ValueError("Invalid search status")
        if (
            type(self.index) is not ActiveIndex
            or type(self.search_policy) is not SearchPolicyManifest
            or type(self.evidence_items) is not tuple
        ):
            raise ValueError("Invalid search result")
        if (self.status == "no_evidence") != (not self.evidence_items):
            raise ValueError("Search status and evidence disagree")


class SearchService:
    """Embed, retrieve, fuse, and group one authorized natural-language query."""

    def __init__(
        self,
        *,
        repository: SearchRepository,
        embeddings: QueryEmbeddingProvider,
        settings: Settings,
        policy_manifest: SearchPolicyManifest,
    ) -> None:
        self._repository = repository
        self._embeddings = embeddings
        self._settings = settings
        self._policy_manifest = policy_manifest

    def search(
        self,
        authorized: AuthorizedSource,
        query: str,
        *,
        limit: int,
    ) -> SearchResult:
        """Return ranked evidence for the fixed active OMF generation."""
        if type(authorized) is not AuthorizedSource or authorized.source_key != "omf":
            raise ValueError("Invalid authorized source")
        if type(query) is not str or not (normalized_query := query.strip()):
            raise ValueError("query must be non-blank")
        if (
            type(limit) is not int
            or limit < 1
            or limit > self._settings.search_max_limit
        ):
            raise ValueError("limit is outside the approved range")
        try:
            policy = self._resolved_policy()
        except SearchUnavailableError:
            raise
        try:
            active = self._repository.active_index(
                authorized,
                self._embeddings.descriptor,
                normalize_embeddings=policy.query_embedding_normalize_embeddings,
            )
        except (NoActiveIndexError, SearchUnavailableError, SourceAccessError):
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise SearchUnavailableError from None
        try:
            query_vector = self._embeddings.embed_query(normalized_query)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise SearchUnavailableError from None
        batch = self._repository.retrieve(
            authorized,
            normalized_query,
            query_vector,
            self._embeddings.descriptor,
            keyword_limit=policy.keyword_candidate_limit,
            vector_limit=policy.vector_candidate_limit,
            normalize_embeddings=policy.query_embedding_normalize_embeddings,
            keyword_similarity_floor=policy.keyword_similarity_floor,
            vector_similarity_floor=policy.vector_similarity_floor,
        )
        if batch.index != active:
            raise SearchUnavailableError
        try:
            keyword = retain_at_or_above(batch.keyword, policy.keyword_similarity_floor)
            vector = retain_at_or_above(batch.vector, policy.vector_similarity_floor)
            fused = reciprocal_rank_fusion(
                keyword=tuple(item.candidate for item in keyword),
                vector=tuple(item.candidate for item in vector),
                k=policy.rrf_k,
                keyword_weight=policy.keyword_weight,
                vector_weight=policy.vector_weight,
            )
            evidence = group_evidence(fused, limit=limit)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise SearchUnavailableError from None
        return SearchResult(
            status="ok" if evidence else "no_evidence",
            index=batch.index,
            search_policy=self._policy_manifest,
            evidence_items=evidence,
        )

    def is_ready(self, authorized: AuthorizedSource) -> bool:
        """Fail closed on any model, database, grant, or active-index problem."""
        try:
            policy = self._resolved_policy()
            return (
                self._embeddings.is_ready() is True
                and self._repository.is_ready(
                    authorized,
                    self._embeddings.descriptor,
                    normalize_embeddings=policy.query_embedding_normalize_embeddings,
                )
                is True
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return False

    def _resolved_policy(self) -> SearchPolicySnapshot:
        """Revalidate startup resolution and query-provider compatibility."""
        try:
            configured = self._settings.search_policy_snapshot()
            policy = self._policy_manifest.snapshot
            descriptor = self._embeddings.descriptor
            if (
                configured != policy
                or configured.config_hash != self._policy_manifest.config_hash
                or policy.calibration_status != "calibrated"
                or descriptor.model_name != policy.query_embedding_model_name
                or descriptor.revision != policy.query_embedding_revision
                or descriptor.dimension != policy.query_embedding_dimension
            ):
                raise SearchUnavailableError
            return policy
        except SearchUnavailableError:
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise SearchUnavailableError from None


__all__ = ["SearchResult", "SearchService"]
