"""Shared errors for the PostgreSQL indexing repository facade."""


class RepositoryInvariantError(ValueError):
    """Raised before persistence when an indexing invariant is violated."""


__all__ = ["RepositoryInvariantError"]
