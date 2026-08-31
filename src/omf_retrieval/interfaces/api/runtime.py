"""Runtime composition kept separate from side-effect-free FastAPI imports."""

import os

from omf_retrieval.application.admin.service import ClientAccessService
from omf_retrieval.application.admin.tokens import AuthorizedSource
from omf_retrieval.application.indexing.config_identity import (
    document_embedding_config_hash,
)
from omf_retrieval.application.search import (
    SearchPolicyManifest,
    SearchResult,
    SearchService,
    SearchUnavailableError,
)
from omf_retrieval.infrastructure.database.repository_auth import (
    PostgresClientRepository,
)
from omf_retrieval.infrastructure.database.repository_policy import (
    PostgresSearchPolicyRepository,
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


def resolve_runtime_search_policy(
    transactions: object, settings: Settings
) -> SearchPolicyManifest:
    """Register and resolve the exact configured policy before serving requests."""
    try:
        snapshot = settings.search_policy_snapshot()
        with transactions.begin() as database_session:  # type: ignore[attr-defined]
            policies = PostgresSearchPolicyRepository(database_session)
            registered = policies.register(snapshot)
            resolved = policies.resolve(snapshot.config_hash)
        if registered != resolved or resolved.snapshot != snapshot:
            raise RuntimeError
        return resolved
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise RuntimeError("Search policy is unavailable") from None


class _UnavailableSearchService:
    """Keep liveness observable while policy startup remains fail-closed."""

    def search(
        self,
        _authorized: AuthorizedSource,
        _query: str,
        *,
        limit: int,
        relevance_level: str = "default",
    ) -> SearchResult:
        del limit, relevance_level
        raise SearchUnavailableError

    def is_ready(self, _authorized: AuthorizedSource) -> bool:
        return False


def build_runtime_app() -> object:
    """Compose PostgreSQL, authorization, Qwen query embedding, and FastAPI."""
    settings = Settings()
    engine = create_database_engine(database_url_from_environment())
    transactions = create_session_factory(engine)
    clients = PostgresClientRepository(transactions)
    access = ClientAccessService(clients)
    try:
        policy_manifest = resolve_runtime_search_policy(transactions, settings)
    except RuntimeError:
        application = create_app(
            access_service=access,
            search_service=_UnavailableSearchService(),
        )
        application.state.database_engine = engine  # type: ignore[attr-defined]
        return application
    embeddings = SentenceTransformerEmbeddingProvider(settings)
    embedding_config = embeddings.embedding_config_snapshot.as_config()
    embedding_hash = document_embedding_config_hash(embedding_config)
    document_config = embedding_config["document"]
    search_repository = PostgresHybridSearchRepository(
        transactions,
        embedding_config_hash=embedding_hash,
        embedding_provider=document_config["provider"],  # type: ignore[arg-type]
    )
    search = SearchService(
        repository=search_repository,
        embeddings=embeddings,
        settings=settings,
        policy_manifest=policy_manifest,
    )
    application = create_app(access_service=access, search_service=search)
    application.state.database_engine = engine  # type: ignore[attr-defined]
    return application


__all__ = [
    "build_runtime_app",
    "database_url_from_environment",
    "resolve_runtime_search_policy",
]
