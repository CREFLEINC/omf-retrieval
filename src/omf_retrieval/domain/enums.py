"""Framework-free enum contracts shared by indexing and search."""

from enum import Enum


class VersionScope(str, Enum):
    """Describe which document version set to search."""

    CURRENT = "current"
    HISTORICAL = "historical"
    ALL = "all"


class DecisionState(str, Enum):
    """Describe the authority of a document decision."""

    CONFIRMED = "confirmed"
    DRAFT = "draft"
    UNKNOWN = "unknown"


class OwnerDomain(str, Enum):
    """Identify the source domain that owns a document."""

    DOCS = "docs"
    UIUX = "uiux"


class IndexRunStatus(str, Enum):
    """Describe an index run lifecycle state."""

    BUILDING = "building"
    READY = "ready"
    ACTIVE = "active"
    PREVIOUS = "previous"
    FAILED = "failed"


class RelationType(str, Enum):
    """Describe a relationship between documents."""

    SUPERSEDES = "supersedes"
    POTENTIAL_CONFLICT = "potential_conflict"


class SearchStatus(str, Enum):
    """Describe the outcome of a search."""

    OK = "ok"
    NO_EVIDENCE = "no_evidence"
