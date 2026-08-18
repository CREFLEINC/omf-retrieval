"""Framework-free validation policies for domain value objects."""

from omf_retrieval.domain.errors import InvariantViolation


def require_inclusive_line_range(*, line_start: int, line_end: int) -> None:
    """Require a one-based inclusive source line range.

    Args:
        line_start: The first source line.
        line_end: The final source line.

    Raises:
        InvariantViolation: If an endpoint is below one or endpoints are reversed.
    """
    if line_start < 1 or line_end < 1 or line_start > line_end:
        raise InvariantViolation("Line range must be one-based and inclusive")


def require_positive_dimension(*, dimension: int) -> None:
    """Require a positive embedding vector dimension.

    Args:
        dimension: The embedding vector dimension.

    Raises:
        InvariantViolation: If the dimension is not positive.
    """
    if dimension <= 0:
        raise InvariantViolation("Embedding dimension must be positive")
