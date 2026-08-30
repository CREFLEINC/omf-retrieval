"""Framework-free contracts for the authenticated search use case."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import isfinite
from typing import Protocol
from uuid import UUID

from omf_retrieval.application.admin.tokens import AuthorizedSource
from omf_retrieval.domain.models import EmbeddingDescriptor

_SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")


class NoActiveIndexError(RuntimeError):
    """Report that the fixed source has no active searchable generation."""

    status_code = 409
    code = "no_active_index"

    def __init__(self) -> None:
        super().__init__("No active index is available.")


class SearchUnavailableError(RuntimeError):
    """Report a safe database or embedding readiness failure."""

    status_code = 503
    code = "service_unavailable"

    def __init__(self) -> None:
        super().__init__("Search service is unavailable.")


@dataclass(frozen=True, slots=True, order=True)
class Origin:
    """One reproducible repository path for canonical document content."""

    source_path: str
    content_hash: str

    def __post_init__(self) -> None:
        if (
            type(self.source_path) is not str
            or not self.source_path.startswith("design/wiki/")
            or not self.source_path.endswith(".md")
            or type(self.content_hash) is not str
            or _SHA256.fullmatch(self.content_hash) is None
        ):
            raise ValueError("Invalid evidence origin")


@dataclass(frozen=True, slots=True)
class ActiveIndex:
    """Coordinates of the active immutable source generation."""

    run_id: UUID
    commit_sha: str

    def __post_init__(self) -> None:
        if (
            type(self.run_id) is not UUID
            or type(self.commit_sha) is not str
            or _GIT_SHA.fullmatch(self.commit_sha) is None
        ):
            raise ValueError("Invalid active index coordinate")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One searchable child with source-backed evidence metadata."""

    chunk_id: UUID
    parent_id: UUID
    heading_path: tuple[str, ...]
    excerpt: str
    line_start: int
    line_end: int
    origins: tuple[Origin, ...]

    def __post_init__(self) -> None:
        if type(self.chunk_id) is not UUID or type(self.parent_id) is not UUID:
            raise ValueError("Invalid candidate identity")
        if type(self.heading_path) is not tuple or any(
            type(heading) is not str or not heading.strip()
            for heading in self.heading_path
        ):
            raise ValueError("Invalid candidate heading path")
        if type(self.excerpt) is not str or not self.excerpt:
            raise ValueError("Invalid candidate excerpt")
        if (
            type(self.line_start) is not int
            or type(self.line_end) is not int
            or self.line_start < 1
            or self.line_end < self.line_start
        ):
            raise ValueError("Invalid candidate line range")
        if (
            type(self.origins) is not tuple
            or not self.origins
            or any(type(origin) is not Origin for origin in self.origins)
            or len(set(self.origins)) != len(self.origins)
        ):
            raise ValueError("Invalid candidate origins")


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """One lane-specific candidate and its raw similarity score."""

    candidate: Candidate
    raw_score: float

    def __post_init__(self) -> None:
        if (
            type(self.candidate) is not Candidate
            or type(self.raw_score) is not float
            or not isfinite(self.raw_score)
            or not -1.0 <= self.raw_score <= 1.0
        ):
            raise ValueError("Invalid scored candidate")


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    """Independent ordered keyword and vector candidates for one active run."""

    index: ActiveIndex
    keyword: tuple[ScoredCandidate, ...]
    vector: tuple[ScoredCandidate, ...]

    def __post_init__(self) -> None:
        if type(self.index) is not ActiveIndex:
            raise ValueError("Invalid candidate index")
        for candidates in (self.keyword, self.vector):
            if type(candidates) is not tuple or any(
                type(candidate) is not ScoredCandidate for candidate in candidates
            ):
                raise ValueError("Invalid candidate sequence")
            identifiers = tuple(
                candidate.candidate.chunk_id for candidate in candidates
            )
            if len(set(identifiers)) != len(identifiers):
                raise ValueError("Candidate sequence contains duplicates")


class SearchRepository(Protocol):
    """Load policy-limited candidates while rechecking authorization in SQL."""

    def active_index(
        self,
        authorized: AuthorizedSource,
        descriptor: EmbeddingDescriptor,
        *,
        normalize_embeddings: bool,
    ) -> ActiveIndex:
        """Resolve the active authorized run before query-model inference."""

    def retrieve(
        self,
        authorized: AuthorizedSource,
        query: str,
        query_vector: tuple[float, ...],
        descriptor: EmbeddingDescriptor,
        *,
        keyword_limit: int,
        vector_limit: int,
        normalize_embeddings: bool,
        keyword_similarity_floor: float,
        vector_similarity_floor: float,
    ) -> CandidateBatch:
        """Return candidates from the active authorized source."""

    def is_ready(
        self,
        authorized: AuthorizedSource,
        descriptor: EmbeddingDescriptor,
        *,
        normalize_embeddings: bool,
    ) -> bool:
        """Return exact DB, grant, active-index, and model-config readiness."""


class QueryEmbeddingProvider(Protocol):
    """Minimal query-side embedding behavior used by search."""

    @property
    def descriptor(self) -> EmbeddingDescriptor:
        """Return the immutable model identity."""

    def embed_query(self, query: str) -> tuple[float, ...]:
        """Generate one normalized query vector."""

    def is_ready(self) -> bool:
        """Return local model readiness without downloading resources."""


__all__ = [
    "ActiveIndex",
    "Candidate",
    "CandidateBatch",
    "NoActiveIndexError",
    "Origin",
    "QueryEmbeddingProvider",
    "SearchRepository",
    "ScoredCandidate",
    "SearchUnavailableError",
]
