"""Database mappings and explicit transaction factory construction."""

from omf_retrieval.infrastructure.database.base import Base
from omf_retrieval.infrastructure.database.models import (
    ApiClient,
    Chunk,
    ChunkEmbedding,
    ClientSourceGrant,
    DocumentContent,
    DocumentOccurrence,
    DocumentParse,
    DocumentRelation,
    IndexConfig,
    IndexRun,
    SearchAuditEvent,
    Section,
    SourceProfile,
)
from omf_retrieval.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)

__all__ = [
    "ApiClient",
    "Base",
    "Chunk",
    "ChunkEmbedding",
    "ClientSourceGrant",
    "DocumentContent",
    "DocumentOccurrence",
    "DocumentParse",
    "DocumentRelation",
    "IndexConfig",
    "IndexRun",
    "SearchAuditEvent",
    "Section",
    "SourceProfile",
    "create_database_engine",
    "create_session_factory",
]
