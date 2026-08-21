"""Offline lazy SentenceTransformer embedding and tokenization adapters."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from math import isclose, isfinite, sqrt
from numbers import Real
from pathlib import Path
from threading import BoundedSemaphore, Condition, Lock, get_ident
from typing import Any

from omf_retrieval.application.indexing.ports import TokenizerDescriptor
from omf_retrieval.domain.models import EmbeddingDescriptor
from omf_retrieval.infrastructure.embedding.provider import (
    EmbeddingBatch,
    EmbeddingVector,
)
from omf_retrieval.settings import Settings

_TOKENIZER_LIBRARY_NAME = "transformers"
_NORMALIZATION_TOLERANCE = 1e-5
_MAX_INPUTS_PER_CALL = 1_000_000


@dataclass(frozen=True, slots=True)
class _RuntimeIdentity:
    model_name: str
    revision: str
    device: str
    cache_dir: str | None


@dataclass(frozen=True, slots=True)
class _LoadedBackend:
    model: Any
    tokenizer: Any
    tokenizer_version: str


class _LazyRuntime:
    """Own one backend load and one serialized inference lane per identity."""

    def __init__(self, identity: _RuntimeIdentity) -> None:
        self.identity = identity
        self._condition = Condition(Lock())
        self._state = "unloaded"
        self._loader_thread_id: int | None = None
        self._backend: _LoadedBackend | Any | None = None
        self._inference_semaphore = BoundedSemaphore(1)
        self._inference_owner_lock = Lock()
        self._inference_owner_thread_id: int | None = None

    def is_ready(self) -> bool:
        """Return loaded state without loading or probing external resources."""
        with self._condition:
            return self._state == "ready"

    def backend(self) -> _LoadedBackend | Any:
        """Load exactly once while allowing other identities to progress."""
        current_thread_id = get_ident()
        with self._condition:
            while self._state == "loading":
                if self._loader_thread_id == current_thread_id:
                    raise RuntimeError("Embedding backend unavailable")
                self._condition.wait()
            if self._state == "ready":
                return self._backend
            if self._state == "failed":
                raise RuntimeError("Embedding backend unavailable")
            self._state = "loading"
            self._loader_thread_id = current_thread_id

        loaded_backend: object | None = None
        failed = False
        try:
            loaded_backend = _load_runtime_backend(self.identity)
        except (KeyboardInterrupt, SystemExit):
            with self._condition:
                self._state = "unloaded"
                self._loader_thread_id = None
                self._condition.notify_all()
            raise
        except Exception:
            failed = True

        with self._condition:
            self._loader_thread_id = None
            if failed or loaded_backend is None:
                self._state = "failed"
                self._condition.notify_all()
            else:
                self._backend = loaded_backend
                self._state = "ready"
                self._condition.notify_all()
                return loaded_backend
        raise RuntimeError("Embedding backend unavailable")

    def inference(self, operation: Any) -> object:
        """Run one model operation through the bounded inference lane."""
        backend = self.backend()
        current_thread_id = get_ident()
        with self._inference_owner_lock:
            if self._inference_owner_thread_id == current_thread_id:
                raise RuntimeError("Embedding backend unavailable")
        self._inference_semaphore.acquire()
        with self._inference_owner_lock:
            self._inference_owner_thread_id = current_thread_id
        result: object | None = None
        failed = False
        try:
            result = operation(backend)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            failed = True
        finally:
            with self._inference_owner_lock:
                self._inference_owner_thread_id = None
            self._inference_semaphore.release()
        if failed:
            raise RuntimeError("Embedding backend unavailable")
        return result


_RUNTIME_REGISTRY_LOCK = Lock()
_RUNTIME_REGISTRY: dict[_RuntimeIdentity, _LazyRuntime] = {}


def _runtime_for(identity: _RuntimeIdentity) -> _LazyRuntime:
    with _RUNTIME_REGISTRY_LOCK:
        runtime = _RUNTIME_REGISTRY.get(identity)
        if runtime is None:
            runtime = _LazyRuntime(identity)
            _RUNTIME_REGISTRY[identity] = runtime
        return runtime


def _load_runtime_backend(identity: _RuntimeIdentity) -> _LoadedBackend:
    """Load pinned local-only model resources after the first real operation."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(
        identity.model_name,
        device=identity.device,
        cache_folder=identity.cache_dir,
        trust_remote_code=False,
        revision=identity.revision,
        local_files_only=True,
        model_kwargs={"attn_implementation": "sdpa"},
        processor_kwargs={"use_fast": True},
    )
    tokenizer = model.tokenizer
    if getattr(tokenizer, "is_fast", False) is not True:
        raise RuntimeError("Tokenizer backend unavailable")
    return _LoadedBackend(
        model=model,
        tokenizer=tokenizer,
        tokenizer_version=version(_TOKENIZER_LIBRARY_NAME),
    )


def _identity(settings: Settings) -> _RuntimeIdentity:
    cache_dir = settings.embedding_cache_dir
    return _RuntimeIdentity(
        model_name=settings.embedding_model_name,
        revision=settings.embedding_model_revision,
        device=settings.embedding_device,
        cache_dir=str(cache_dir) if isinstance(cache_dir, Path) else None,
    )


def _validated_vectors(
    result: object, *, expected_count: int, dimension: int
) -> EmbeddingBatch:
    materialized = _materialize_bounded_sequence(result, maximum_items=expected_count)
    if materialized is None or len(materialized) != expected_count:
        raise ValueError("Embedding backend returned malformed vectors")

    vectors: list[EmbeddingVector] = []
    for raw_vector in materialized:
        values = _materialize_bounded_sequence(raw_vector, maximum_items=dimension)
        if values is None or len(values) != dimension:
            raise ValueError("Embedding backend returned malformed vectors")
        if any(
            isinstance(value, bool) or not isinstance(value, Real) for value in values
        ):
            raise ValueError("Embedding backend returned malformed vectors")
        converted: list[float] = []
        conversion_failed = False
        try:
            converted = [float(value) for value in values]
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            conversion_failed = True
        if conversion_failed:
            raise ValueError("Embedding backend returned malformed vectors")
        vector = tuple(converted)
        if not all(isfinite(value) for value in vector):
            raise ValueError("Embedding backend returned malformed vectors")
        norm = sqrt(sum(value * value for value in vector))
        if not isclose(
            norm,
            1.0,
            rel_tol=_NORMALIZATION_TOLERANCE,
            abs_tol=_NORMALIZATION_TOLERANCE,
        ):
            raise ValueError("Embedding backend returned malformed vectors")
        vectors.append(vector)
    return tuple(vectors)


def _materialize_documents(documents: object) -> tuple[str, ...]:
    if isinstance(documents, (str, bytes, bytearray)):
        raise ValueError("Invalid embedding input")
    values: list[object] = []
    failed = False
    declared_length = -1
    try:
        declared_length = len(documents)  # type: ignore[arg-type]
        if not 0 <= declared_length <= _MAX_INPUTS_PER_CALL:
            failed = True
        else:
            iterator = iter(documents)  # type: ignore[arg-type]
            for _ in range(declared_length + 1):
                try:
                    values.append(next(iterator))
                except StopIteration:
                    break
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        failed = True
    if failed or len(values) != declared_length:
        raise ValueError("Invalid embedding input")
    if any(type(value) is not str or not value.strip() for value in values):
        raise ValueError("Invalid embedding input")
    return tuple(values)  # type: ignore[return-value]


def _validate_adapter_settings(settings: Settings) -> None:
    if settings.environment in {"development", "test"}:
        if settings.embedding_device != "cpu":
            raise ValueError("Invalid embedding device")


def _materialize_bounded_sequence(
    value: object, *, maximum_items: int
) -> tuple[object, ...] | None:
    values: list[object] = []
    failed = False
    declared_length = -1
    try:
        declared_length = len(value)  # type: ignore[arg-type]
        if not 0 <= declared_length <= maximum_items:
            failed = True
        else:
            iterator = iter(value)  # type: ignore[arg-type]
            for _ in range(declared_length + 1):
                try:
                    values.append(next(iterator))
                except StopIteration:
                    break
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        failed = True
    if failed or len(values) != declared_length:
        return None
    return tuple(values)


def _validated_tokenization(
    result: object, *, text_length: int
) -> tuple[tuple[int, ...], tuple[tuple[int, int], ...]]:
    raw_token_ids: object | None = None
    raw_offsets: object | None = None
    access_failed = False
    try:
        raw_token_ids = result["input_ids"]  # type: ignore[index]
        raw_offsets = result["offset_mapping"]  # type: ignore[index]
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        access_failed = True
    if access_failed:
        raise ValueError("Tokenizer backend returned malformed data")

    token_ids = _materialize_bounded_sequence(raw_token_ids, maximum_items=text_length)
    offsets = _materialize_bounded_sequence(raw_offsets, maximum_items=text_length)
    if token_ids is None or offsets is None or len(token_ids) != len(offsets):
        raise ValueError("Tokenizer backend returned malformed data")
    if any(type(token_id) is not int or token_id < 0 for token_id in token_ids):
        raise ValueError("Tokenizer backend returned malformed data")

    validated_offsets: list[tuple[int, int]] = []
    previous_end = 0
    for raw_offset in offsets:
        if (
            type(raw_offset) is not tuple
            or len(raw_offset) != 2
            or type(raw_offset[0]) is not int
            or type(raw_offset[1]) is not int
        ):
            raise ValueError("Tokenizer backend returned malformed data")
        start, end = raw_offset
        if start < previous_end or start < 0 or end <= start or end > text_length:
            raise ValueError("Tokenizer backend returned malformed data")
        validated_offsets.append((start, end))
        previous_end = end
    return tuple(token_ids), tuple(validated_offsets)  # type: ignore[return-value]


class SentenceTransformerEmbeddingProvider:
    """Generate fixed-revision embeddings without eager model loading."""

    def __init__(self, settings: Settings) -> None:
        """Bind validated settings while leaving heavy resources unloaded."""
        _validate_adapter_settings(settings)
        if settings.query_instruction.count("{query}") != 1:
            raise ValueError("Invalid query instruction")
        self._settings = settings
        self._runtime = _runtime_for(_identity(settings))

    @property
    def descriptor(self) -> EmbeddingDescriptor:
        """Return the configured immutable embedding identity."""
        return EmbeddingDescriptor(
            model_name=self._settings.embedding_model_name,
            revision=self._settings.embedding_model_revision,
            dimension=self._settings.embedding_dimension,
        )

    def embed_query(self, query: str) -> EmbeddingVector:
        """Embed a query after applying the configured instruction."""
        if type(query) is not str or not query.strip():
            raise ValueError("Invalid embedding input")
        instructed_query = self._settings.query_instruction.replace("{query}", query, 1)
        return self._embed((instructed_query,))[0]

    def embed_documents(self, documents: Sequence[str]) -> EmbeddingBatch:
        """Embed documents in input order."""
        materialized = _materialize_documents(documents)
        if not materialized:
            return ()
        return self._embed(materialized)

    def is_ready(self) -> bool:
        """Report loaded state without cache I/O or network access.

        Task 7C adds snapshot-manifest integrity to the API readiness policy.
        """
        return self._runtime.is_ready()

    def _embed(self, inputs: tuple[str, ...]) -> EmbeddingBatch:
        vectors: list[EmbeddingVector] = []
        batch_size = self._settings.embedding_batch_size
        for batch_start in range(0, len(inputs), batch_size):
            batch = inputs[batch_start : batch_start + batch_size]

            def encode(backend: Any, encoded_batch: tuple[str, ...] = batch) -> object:
                return backend.model.encode(
                    list(encoded_batch),
                    batch_size=batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                    convert_to_tensor=False,
                    normalize_embeddings=True,
                )

            result = self._runtime.inference(encode)
            vectors.extend(
                _validated_vectors(
                    result,
                    expected_count=len(batch),
                    dimension=self._settings.embedding_dimension,
                )
            )
        return tuple(vectors)


class SentenceTransformerTokenCounter:
    """Provide Qwen token IDs and exact source-backed character offsets."""

    def __init__(self, settings: Settings) -> None:
        """Bind tokenizer identity without eager tokenizer loading."""
        _validate_adapter_settings(settings)
        self._settings = settings
        self._runtime = _runtime_for(_identity(settings))

    @property
    def descriptor(self) -> TokenizerDescriptor:
        """Return the exact tokenizer behavior identity used by chunking."""
        return TokenizerDescriptor(
            model_name=self._settings.embedding_model_name,
            revision=self._settings.embedding_model_revision,
            library_name=_TOKENIZER_LIBRARY_NAME,
            library_version=version(_TOKENIZER_LIBRARY_NAME),
            add_special_tokens=False,
        )

    def encode(self, text: str) -> tuple[int, ...]:
        """Return source token IDs without special tokens."""
        if type(text) is not str:
            raise ValueError("Invalid tokenizer input")
        if text == "":
            return ()
        token_ids, _ = self._tokenize(text)
        return token_ids

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        """Return one source-backed half-open span per token.

        Real Qwen emoji and byte-fallback offset compatibility remains a server/GPU
        model checkpoint in Task 7C/17.
        """
        if type(text) is not str:
            raise ValueError("Invalid tokenizer input")
        if text == "":
            return ()
        _, offsets = self._tokenize(text)
        return offsets

    def _tokenize(
        self, text: str
    ) -> tuple[tuple[int, ...], tuple[tuple[int, int], ...]]:
        def tokenize(backend: Any) -> object:
            return backend.tokenizer(
                text,
                add_special_tokens=False,
                return_offsets_mapping=True,
                return_attention_mask=False,
                return_token_type_ids=False,
            )

        result = self._runtime.inference(tokenize)
        return _validated_tokenization(result, text_length=len(text))
