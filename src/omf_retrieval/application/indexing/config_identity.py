"""Canonical JSON identities shared by indexing application ports."""

from math import isfinite

from omf_retrieval.application.indexing.hashing import config_hash

_DOCUMENT_KEYS = {
    "provider",
    "model_name",
    "revision",
    "dimension",
    "normalize_embeddings",
    "library_name",
    "library_version",
}
_EMBEDDING_KEYS = {"document", "query"}
_QUERY_KEYS = {"instruction"}
_RETRIEVAL_KEYS = {
    "k",
    "keyword_weight",
    "vector_weight",
    "keyword_similarity_floor",
    "vector_similarity_floor",
    "evidence_floor_status",
}


class IndexConfigValidationError(ValueError):
    """Raised when a canonical index configuration identity is invalid."""


def document_embedding_config_hash(embedding_config: object) -> str:
    """Hash only document-vector behavior, excluding query behavior."""
    document, _ = validated_embedding_config(embedding_config)
    return config_hash(document)


def full_index_config_hash(
    *,
    parser_config: object,
    chunk_config: object,
    tokenizer_config: object,
    embedding_config: object,
    rrf_config: object,
) -> str:
    """Hash all five exact persisted configuration snapshots."""
    for name, snapshot in (
        ("parser_config", parser_config),
        ("chunk_config", chunk_config),
        ("tokenizer_config", tokenizer_config),
    ):
        if type(snapshot) is not dict:
            raise IndexConfigValidationError(f"{name} must be an exact JSON object")
    validated_embedding_config(embedding_config)
    return config_hash(
        {
            "parser_config": parser_config,
            "chunk_config": chunk_config,
            "tokenizer_config": tokenizer_config,
            "embedding_config": embedding_config,
            "rrf_config": rrf_config,
        }
    )


def validated_embedding_config(
    embedding_config: object,
) -> tuple[dict[str, object], dict[str, object]]:
    """Validate and return the exact document/query JSON objects."""
    if type(embedding_config) is not dict or set(embedding_config) != _EMBEDDING_KEYS:
        raise IndexConfigValidationError(
            "embedding_config keys must be exactly document and query"
        )
    document = embedding_config["document"]
    query = embedding_config["query"]
    if type(document) is not dict or set(document) != _DOCUMENT_KEYS:
        raise IndexConfigValidationError(
            "document embedding keys do not match contract"
        )
    if type(query) is not dict or set(query) != _QUERY_KEYS:
        raise IndexConfigValidationError("query embedding keys do not match contract")
    for field in (
        "provider",
        "model_name",
        "revision",
        "library_name",
        "library_version",
    ):
        require_nonblank_string(document[field])
    if type(document["dimension"]) is not int or document["dimension"] <= 0:
        raise IndexConfigValidationError("dimension must be a positive exact integer")
    if type(document["normalize_embeddings"]) is not bool:
        raise IndexConfigValidationError(
            "normalize_embeddings must be an exact boolean"
        )
    require_nonblank_string(query["instruction"])
    return document, query


def validated_retrieval_config(value: object) -> dict[str, object]:
    """Validate the exact persisted ranking and evidence-floor snapshot."""
    if type(value) is not dict or set(value) != _RETRIEVAL_KEYS:
        raise IndexConfigValidationError("retrieval config keys do not match contract")
    if type(value["k"]) is not int or value["k"] <= 0:
        raise IndexConfigValidationError("RRF k must be a positive exact integer")
    for field in ("keyword_weight", "vector_weight"):
        weight = value[field]
        if type(weight) is not float or not isfinite(weight) or weight <= 0.0:
            raise IndexConfigValidationError(
                "retrieval weights must be positive finite exact floats"
            )
    for field in ("keyword_similarity_floor", "vector_similarity_floor"):
        floor = value[field]
        if type(floor) is not float or not isfinite(floor) or not 0.0 <= floor <= 1.0:
            raise IndexConfigValidationError(
                "evidence floors must be finite exact floats between 0 and 1"
            )
    status = value["evidence_floor_status"]
    if type(status) is not str or status not in {
        "calibration_pending",
        "calibrated",
    }:
        raise IndexConfigValidationError("evidence floor status is invalid")
    return value


def require_nonblank_string(value: object) -> None:
    """Require a nonblank exact identity string."""
    if type(value) is not str or not value.strip():
        raise IndexConfigValidationError("identity strings must be nonblank and exact")


__all__ = [
    "IndexConfigValidationError",
    "document_embedding_config_hash",
    "full_index_config_hash",
    "require_nonblank_string",
    "validated_embedding_config",
    "validated_retrieval_config",
]
