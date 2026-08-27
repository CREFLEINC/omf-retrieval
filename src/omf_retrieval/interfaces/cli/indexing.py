"""Fixed-commit OMF indexing CLI composition."""

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import typer
from sqlalchemy.orm import Session

from omf_retrieval.application.indexing.activation import ActivationService
from omf_retrieval.application.indexing.pipeline import (
    FixedCommitIndexWorkflow,
    TransactionalIndexPipeline,
)
from omf_retrieval.application.indexing.ports import ChunkConfig
from omf_retrieval.application.indexing.service import IndexService
from omf_retrieval.application.search.policy import retrieval_config_snapshot
from omf_retrieval.domain.enums import IndexRunStatus
from omf_retrieval.infrastructure.database.repositories import (
    PostgresIndexRepository,
)
from omf_retrieval.infrastructure.database.repository_activation import (
    PostgresActivationRepository,
)
from omf_retrieval.infrastructure.database.repository_config import (
    EmbeddingAdapterIdentity,
    PostgresIndexConfigurationRepository,
)
from omf_retrieval.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from omf_retrieval.infrastructure.embedding.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
    SentenceTransformerTokenCounter,
)
from omf_retrieval.infrastructure.source.chunker import (
    ParentChildChunker,
    chunk_config_identity_hash,
)
from omf_retrieval.infrastructure.source.git_archive import GitArchiveSnapshotProvider
from omf_retrieval.infrastructure.source.markdown import (
    PARSER_VERSION,
    MarkdownItParser,
)
from omf_retrieval.infrastructure.source.profiles import omf_profile
from omf_retrieval.interfaces.api.runtime import database_url_from_environment
from omf_retrieval.settings import Settings


class _NoOpAudit:
    def write(self, _event: object) -> None:
        return None


def run_fixed_index() -> dict[str, object]:
    """Build and immediately activate the committed OMF source profile."""
    raw_source_repo = os.environ.get("OMF_RETRIEVAL_SOURCE_REPO")
    if raw_source_repo is None or not raw_source_repo.strip():
        raise RuntimeError("Source repository is unavailable")
    source_repo = Path(raw_source_repo)
    settings = Settings()
    profile = omf_profile()
    if profile.commit_sha is None:
        raise RuntimeError("Fixed source commit is unavailable")
    engine = create_database_engine(database_url_from_environment())
    transactions = create_session_factory(engine)
    embeddings = SentenceTransformerEmbeddingProvider(settings)
    tokenizer = SentenceTransformerTokenCounter(settings)
    chunk_config = ChunkConfig(
        parent_context_max_tokens=settings.parent_context_max_tokens
    )
    chunk_hash = chunk_config_identity_hash(chunk_config, tokenizer.descriptor)
    chunker = ParentChildChunker(tokenizer, tokenizer.descriptor, chunk_config)
    embedding_snapshot = embeddings.embedding_config_snapshot.as_config()
    document_snapshot = embedding_snapshot["document"]
    if type(document_snapshot) is not dict:
        raise RuntimeError("Embedding configuration is unavailable")
    adapter = EmbeddingAdapterIdentity(
        provider=document_snapshot["provider"],
        normalize_embeddings=document_snapshot["normalize_embeddings"],
        library_name=document_snapshot["library_name"],
        library_version=document_snapshot["library_version"],
    )
    parser_config = {"version": PARSER_VERSION}
    chunk_snapshot = {"hash": chunk_hash}
    tokenizer_snapshot = asdict(tokenizer.descriptor)
    rrf_config = retrieval_config_snapshot(settings)
    try:
        with transactions.begin() as session:
            binding = PostgresIndexConfigurationRepository(session).ensure(
                profile=profile,
                parser_config=parser_config,
                chunk_config=chunk_snapshot,
                tokenizer_config=tokenizer_snapshot,
                embedding_config=embedding_snapshot,
                rrf_config=rrf_config,
            )

        def repository_factory(transaction: object) -> PostgresIndexRepository:
            if not isinstance(transaction, Session):
                raise TypeError("Invalid index transaction")
            return PostgresIndexRepository(
                session=transaction,
                source_profile_id=binding.source_profile_id,
                index_config_id=binding.index_config_id,
                embedding_descriptor=embeddings.descriptor,
                embedding_adapter_identity=adapter,
            )

        pipeline = TransactionalIndexPipeline(
            transactions=transactions,
            repository_factory=repository_factory,
            service_factory=lambda repository: IndexService(
                repository=repository,
                parser=MarkdownItParser(),
                chunker=chunker,
                embeddings=embeddings,
                parser_version=PARSER_VERSION,
                chunk_config_hash=chunk_hash,
                embedding_config_hash=binding.embedding_config_hash,
                embedding_dimension=embeddings.descriptor.dimension,
            ),
            snapshot_provider=GitArchiveSnapshotProvider(profile),
            source_repo=source_repo,
        )
        activation = ActivationService(
            transactions=transactions,
            repository_factory=lambda transaction: PostgresActivationRepository(
                transaction  # type: ignore[arg-type]
            ),
            embedding_provider=embeddings,
            audit_logger=_NoOpAudit(),
            clock=lambda: datetime.now(UTC),
        )
        result = FixedCommitIndexWorkflow(
            pipeline=pipeline,
            activation=activation,
            source_key="omf",
            commit_sha=profile.commit_sha,
            actor="index",
        ).index()
        if result.status is IndexRunStatus.FAILED:
            raise RuntimeError("Indexing did not produce an active generation")
        return {
            "run_id": str(result.run_id),
            "status": result.status.value,
            "occurrence_count": result.occurrence_count,
            "unique_content_count": result.unique_content_count,
        }
    finally:
        engine.dispose()


def index_command() -> None:
    """Index and activate the fixed OMF commit with safe failure output."""
    try:
        result = run_fixed_index()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        typer.echo("Indexing failed", err=True)
        raise typer.Exit(code=4) from None
    typer.echo(json.dumps(result, sort_keys=True, separators=(",", ":")))


__all__ = ["index_command", "run_fixed_index"]
