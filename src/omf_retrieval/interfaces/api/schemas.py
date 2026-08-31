"""Strict public JSON schemas for the MVP HTTP surface."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator


class SearchRequest(BaseModel):
    """Accept only a natural-language query and the approved evidence limit."""

    model_config = ConfigDict(extra="forbid")

    query: StrictStr
    limit: StrictInt = Field(default=5, ge=1, le=20)
    relevance_level: Literal["default", "strict"] = "default"

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must be non-blank")
        return normalized


class IndexResponse(BaseModel):
    run_id: UUID
    commit_sha: str


class SearchPolicyResponse(BaseModel):
    policy_id: UUID
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class MatchResponse(BaseModel):
    excerpt: str
    line_start: int
    line_end: int
    keyword_rank: int | None
    vector_rank: int | None
    rrf_score: float


class OriginResponse(BaseModel):
    source_path: str
    content_hash: str


class EvidenceResponse(BaseModel):
    rank: int
    heading_path: list[str]
    matches: list[MatchResponse]
    origins: list[OriginResponse]


class SearchResponse(BaseModel):
    request_id: str
    status: Literal["ok", "no_evidence"]
    index: IndexResponse
    search_policy: SearchPolicyResponse
    evidence_items: list[EvidenceResponse]


class ErrorResponse(BaseModel):
    request_id: str
    code: str
    message: str


__all__ = ["ErrorResponse", "SearchRequest", "SearchResponse"]
