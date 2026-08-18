"""Framework-free immutable value objects for retrieval semantics."""

from dataclasses import dataclass
from datetime import date

from omf_retrieval.domain.enums import DecisionState, OwnerDomain, VersionScope
from omf_retrieval.domain.errors import InvariantViolation
from omf_retrieval.domain.policies import (
    require_inclusive_line_range,
    require_positive_dimension,
)


@dataclass(frozen=True)
class LineRange:
    """Represent an inclusive, one-based source line range.

    Args:
        line_start: The first one-based source line.
        line_end: The last one-based source line.

    Raises:
        InvariantViolation: If the range is not inclusive and one-based.
    """

    line_start: int
    line_end: int

    def __post_init__(self) -> None:
        """Validate the immutable source coordinates."""
        require_inclusive_line_range(
            line_start=self.line_start,
            line_end=self.line_end,
        )


@dataclass(frozen=True)
class DocumentMetadata:
    """Represent typed metadata parsed from a source document.

    Args:
        document_date: Optional source document date.
        version: Optional source document version.
        version_scope: Whether the version is current, historical, or all.
        decision_state: Authority state of the source decision.
        owner_domain: Domain responsible for the source content.
    """

    document_date: date | None
    version: str | None
    version_scope: VersionScope
    decision_state: DecisionState
    owner_domain: OwnerDomain


@dataclass(frozen=True)
class EmbeddingDescriptor:
    """Represent an embedding model identity and output shape.

    Args:
        model_name: Non-empty embedding model identifier.
        revision: Non-empty immutable model revision.
        dimension: Positive embedding vector dimension.

    Raises:
        InvariantViolation: If the model identity or dimension is invalid.
    """

    model_name: str
    revision: str
    dimension: int

    def __post_init__(self) -> None:
        """Validate the embedding identity and vector dimension."""
        if not self.model_name.strip():
            raise InvariantViolation("Embedding model_name must be non-empty")
        if not self.revision.strip():
            raise InvariantViolation("Embedding revision must be non-empty")
        require_positive_dimension(dimension=self.dimension)
