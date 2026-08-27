"""Unit tests for framework-free domain value objects and policies."""

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from omf_retrieval.domain.enums import (
    DecisionState,
    IndexRunStatus,
    OwnerDomain,
    RelationType,
    SearchStatus,
    VersionScope,
)
from omf_retrieval.domain.errors import DomainError, InvariantViolation
from omf_retrieval.domain.models import (
    DocumentMetadata,
    EmbeddingDescriptor,
    LineRange,
)
from omf_retrieval.domain.policies import (
    require_inclusive_line_range,
    require_positive_dimension,
)


def test_domain_enums_expose_approved_wire_values() -> None:
    """Public enums preserve the approved serialization values."""
    assert [member.value for member in VersionScope] == ["current", "historical", "all"]
    assert [member.value for member in DecisionState] == [
        "confirmed",
        "draft",
        "unknown",
    ]
    assert [member.value for member in OwnerDomain] == ["docs", "uiux"]
    assert [member.value for member in IndexRunStatus] == [
        "building",
        "ready",
        "active",
        "previous",
        "archived",
        "failed",
    ]
    assert [member.value for member in RelationType] == [
        "supersedes",
        "potential_conflict",
    ]
    assert [member.value for member in SearchStatus] == ["ok", "no_evidence"]


def test_line_range_is_immutable_and_preserves_inclusive_coordinates() -> None:
    """Valid one-based source coordinates form an immutable value object."""
    line_range = LineRange(line_start=3, line_end=3)

    assert line_range == LineRange(line_start=3, line_end=3)
    with pytest.raises(FrozenInstanceError):
        line_range.line_end = 4  # type: ignore[misc]


@pytest.mark.parametrize(
    ("line_start", "line_end"),
    [(0, 1), (1, 0), (3, 2)],
)
def test_line_range_rejects_non_inclusive_coordinates(
    line_start: int, line_end: int
) -> None:
    """Invalid one-based source coordinates raise the domain invariant error."""
    with pytest.raises(InvariantViolation):
        LineRange(line_start=line_start, line_end=line_end)


def test_line_range_policy_rejects_reversed_coordinates() -> None:
    """The reusable line-range policy rejects reversed endpoints."""
    with pytest.raises(InvariantViolation):
        require_inclusive_line_range(line_start=5, line_end=4)


def test_embedding_descriptor_requires_nonempty_identity_and_dimension() -> None:
    """Embedding identity cannot omit its model, revision, or positive dimension."""
    descriptor = EmbeddingDescriptor(
        model_name="Qwen/Qwen3-Embedding-0.6B",
        revision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        dimension=1024,
    )

    assert descriptor.dimension == 1024
    with pytest.raises(InvariantViolation):
        EmbeddingDescriptor(model_name="", revision="revision", dimension=1024)
    with pytest.raises(InvariantViolation):
        EmbeddingDescriptor(model_name="model", revision="", dimension=1024)
    with pytest.raises(InvariantViolation):
        EmbeddingDescriptor(model_name="model", revision="revision", dimension=0)


def test_positive_dimension_policy_rejects_zero() -> None:
    """The reusable dimension policy rejects zero dimensions."""
    with pytest.raises(InvariantViolation):
        require_positive_dimension(dimension=0)


def test_document_metadata_preserves_typed_document_state() -> None:
    """Document metadata retains optional date/version and enum state."""
    metadata = DocumentMetadata(
        document_date=date(2026, 8, 13),
        version="v1.0",
        version_scope=VersionScope.CURRENT,
        decision_state=DecisionState.CONFIRMED,
        owner_domain=OwnerDomain.DOCS,
    )

    assert metadata.owner_domain is OwnerDomain.DOCS
    assert metadata.version == "v1.0"


def test_domain_errors_are_value_errors() -> None:
    """Domain invariant failures remain catchable as value errors."""
    assert issubclass(InvariantViolation, DomainError)
    assert issubclass(DomainError, ValueError)
