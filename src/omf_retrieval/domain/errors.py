"""Temporary domain error scaffold for test-first implementation."""


class DomainError(ValueError):
    """Base class for domain validation failures."""


class InvariantViolation(DomainError):
    """Raised when a domain invariant is violated."""
