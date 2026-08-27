"""Runtime composition kept separate from side-effect-free FastAPI imports."""

import os

from omf_retrieval.application.admin.service import ClientAccessService
from omf_retrieval.application.indexing.config_identity import (
    document_embedding_config_hash,
)
from omf_retrieval.application.search import SearchService
from omf_retrieval.application.search.policy import retrieval_config_snapshot
from omf_retrieval.infrastructure.database.repository_auth import (
    PostgresClientRepository,
)
from omf_retrieval.infrastructure.database.search import (
    PostgresHybridSearchRepository,
)
from omf_retrieval.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from omf_retrieval.infrastructure.embedding.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
)
from omf_retrieval.interfaces.api.app import create_app
from omf_retrieval.settings import Settings


def database_url_from_environment() -> str:
    """Load the database URL without ever formatting it into an error."""
    value = os.environ.get("OMF_RETRIEVAL_DATABASE_URL")
    if type(value) is not str or not value.strip():
        raise RuntimeError("Database configuration is unavailable")
    return value


def build_runtime_app() -> object:
    """Compose PostgreSQL, authorization, Qwen query embedding, and FastAPI."""
    settings = Settings()
    engine = create_database_engine(database_url_from_environment())
    transactions = create_session_factory(engine)
    embeddings = SentenceTransformerEmbeddingProvider(settings)
    embedding_hash = document_embedding_config_hash(
        embeddings.embedding_config_snapshot.as_config()
    )
    clients = PostgresClientRepository(transactions)
    access = ClientAccessService(clients)
    search_repository = PostgresHybridSearchRepository(
        transactions,
        embedding_config_hash=embedding_hash,
        retrieval_config=retrieval_config_snapshot(settings),
    )
    search = SearchService(
        repository=search_repository,
        embeddings=embeddings,
        settings=settings,
    )
    application = create_app(access_service=access, search_service=search)
    application.state.database_engine = engine  # type: ignore[attr-defined]
    return application


__all__ = ["build_runtime_app", "database_url_from_environment"]
