"""Unit tests for deterministic chunking contracts and identities."""

import re
from dataclasses import FrozenInstanceError, replace
from importlib import import_module

import pytest

from omf_retrieval.application.indexing.ports import ChunkConfig, TokenizerDescriptor
from omf_retrieval.infrastructure.source.chunker import (
    CHUNKER_VERSION,
    chunk_config_identity_hash,
)


class _IntSubclass(int):
    """Exercise rejection of integer subclasses at contract boundaries."""


class _StrSubclass(str):
    """Exercise rejection of string subclasses at contract boundaries."""


class _TupleSubclass(tuple):
    """Exercise rejection of tuple subclasses at contract boundaries."""


class _FakeTokenCounter:
    """Provide deterministic source-backed tokens for the protocol contract."""

    def encode(self, text: str) -> tuple[int, ...]:
        """Map each source character to one deterministic token."""
        return tuple(ord(character) for character in text)

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        """Map every fake token to its exact one-character source slice."""
        return tuple((index, index + 1) for index in range(len(text)))


def test_chunk_contract_exposes_stable_version_and_default_config() -> None:
    """Chunking exposes the approved stable identity and token limits."""
    assert CHUNKER_VERSION == "parent-child-v1"
    assert ChunkConfig() == ChunkConfig(
        target_tokens=400,
        soft_max_tokens=600,
        overlap_tokens=64,
        atomic_max_tokens=800,
        parent_context_max_tokens=1200,
    )


def test_tokenizer_descriptor_accepts_only_exact_immutable_identity_values() -> None:
    """Tokenizer identity is immutable, slotted, and free of raw credentials."""
    descriptor = TokenizerDescriptor(
        model_name="Qwen/Qwen3-Embedding-0.6B",
        revision="0123456789abcdef",
        library_name="transformers",
        library_version="5.15.0",
        add_special_tokens=False,
    )

    assert descriptor.model_name == "Qwen/Qwen3-Embedding-0.6B"
    assert descriptor.revision == "0123456789abcdef"
    assert descriptor.library_name == "transformers"
    assert descriptor.library_version == "5.15.0"
    assert descriptor.add_special_tokens is False
    assert not hasattr(descriptor, "__dict__")
    with pytest.raises(FrozenInstanceError):
        descriptor.revision = "changed"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("model_name", ""),
        ("model_name", " \t"),
        ("model_name", _StrSubclass("model")),
        ("revision", ""),
        ("revision", "\n"),
        ("revision", _StrSubclass("revision")),
        ("library_name", ""),
        ("library_name", " "),
        ("library_name", _StrSubclass("library")),
        ("library_version", ""),
        ("library_version", "\t"),
        ("library_version", _StrSubclass("1.0")),
        ("add_special_tokens", 0),
        ("add_special_tokens", 1),
    ],
)
def test_tokenizer_descriptor_rejects_invalid_exact_values(
    field_name: str, invalid_value: object
) -> None:
    """Blank, subclassed, and non-boolean descriptor fields are rejected."""
    values = {
        "model_name": "model",
        "revision": "revision",
        "library_name": "library",
        "library_version": "1.0",
        "add_special_tokens": False,
    }
    values[field_name] = invalid_value

    with pytest.raises(ValueError):
        TokenizerDescriptor(**values)


def test_chunk_config_accepts_approved_boundary_values_and_is_immutable() -> None:
    """The smallest coherent token limits form an immutable configuration."""
    config = ChunkConfig(
        target_tokens=1,
        soft_max_tokens=1,
        overlap_tokens=0,
        atomic_max_tokens=1,
        parent_context_max_tokens=1,
    )

    assert config.target_tokens == 1
    assert not hasattr(config, "__dict__")
    with pytest.raises(FrozenInstanceError):
        config.target_tokens = 2


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("target_tokens", True),
        ("target_tokens", 1.0),
        ("target_tokens", _IntSubclass(400)),
        ("soft_max_tokens", False),
        ("soft_max_tokens", 600.0),
        ("soft_max_tokens", _IntSubclass(600)),
        ("overlap_tokens", True),
        ("overlap_tokens", 64.0),
        ("overlap_tokens", _IntSubclass(64)),
        ("atomic_max_tokens", False),
        ("atomic_max_tokens", 800.0),
        ("atomic_max_tokens", _IntSubclass(800)),
        ("parent_context_max_tokens", True),
        ("parent_context_max_tokens", 1200.0),
        ("parent_context_max_tokens", _IntSubclass(1200)),
    ],
)
def test_chunk_config_rejects_non_exact_integer_fields(
    field_name: str, invalid_value: object
) -> None:
    """Booleans, floats, and integer subclasses cannot enter config identity."""
    values = {
        "target_tokens": 400,
        "soft_max_tokens": 600,
        "overlap_tokens": 64,
        "atomic_max_tokens": 800,
        "parent_context_max_tokens": 1200,
    }
    values[field_name] = invalid_value

    with pytest.raises(ValueError):
        ChunkConfig(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_tokens": 0},
        {"soft_max_tokens": 0},
        {"overlap_tokens": -1},
        {"atomic_max_tokens": 0},
        {"parent_context_max_tokens": 0},
        {"target_tokens": 601},
        {"soft_max_tokens": 801},
        {"overlap_tokens": 400},
    ],
)
def test_chunk_config_rejects_incoherent_token_boundaries(
    overrides: dict[str, int],
) -> None:
    """Non-positive or incorrectly ordered token boundaries are rejected."""
    values = {
        "target_tokens": 400,
        "soft_max_tokens": 600,
        "overlap_tokens": 64,
        "atomic_max_tokens": 800,
        "parent_context_max_tokens": 1200,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        ChunkConfig(**values)


def test_chunk_config_identity_hash_matches_the_approved_exact_payload() -> None:
    """Chunk identity hashes only approved chunker and tokenizer coordinates."""
    config = ChunkConfig()
    descriptor = TokenizerDescriptor(
        model_name="Qwen/Qwen3-Embedding-0.6B",
        revision="0123456789abcdef",
        library_name="transformers",
        library_version="5.15.0",
        add_special_tokens=False,
    )

    digest = chunk_config_identity_hash(config, descriptor)

    assert digest == "8d792a133589cf9a85c2ecb963005d55d2b0950f7d5cfdb1b0cee405dd233395"
    assert re.fullmatch(r"[0-9a-f]{64}", digest) is not None
    with pytest.raises(TypeError):
        chunk_config_identity_hash(config, descriptor, parser_version="markdown-v1")


def test_chunk_config_identity_hash_is_deterministic() -> None:
    """Equivalent value objects always produce the same identity digest."""
    first_config = ChunkConfig()
    second_config = ChunkConfig()
    first_descriptor = TokenizerDescriptor(
        model_name="model",
        revision="revision",
        library_name="library",
        library_version="1.0",
        add_special_tokens=False,
    )
    second_descriptor = TokenizerDescriptor(
        model_name="model",
        revision="revision",
        library_name="library",
        library_version="1.0",
        add_special_tokens=False,
    )

    assert chunk_config_identity_hash(
        first_config, first_descriptor
    ) == chunk_config_identity_hash(second_config, second_descriptor)


def test_chunk_config_identity_hash_changes_with_every_config_field() -> None:
    """No approved numeric chunk setting can change without invalidating reuse."""
    config = ChunkConfig()
    descriptor = TokenizerDescriptor(
        model_name="model",
        revision="revision",
        library_name="library",
        library_version="1.0",
        add_special_tokens=False,
    )
    baseline = chunk_config_identity_hash(config, descriptor)

    changed_configs = (
        replace(config, target_tokens=401),
        replace(config, soft_max_tokens=601),
        replace(config, overlap_tokens=65),
        replace(config, atomic_max_tokens=801),
        replace(config, parent_context_max_tokens=1201),
    )

    assert all(
        chunk_config_identity_hash(changed_config, descriptor) != baseline
        for changed_config in changed_configs
    )


def test_chunk_config_identity_hash_changes_with_every_tokenizer_field() -> None:
    """No tokenizer behavior coordinate can change without invalidating reuse."""
    config = ChunkConfig()
    descriptor = TokenizerDescriptor(
        model_name="model",
        revision="revision",
        library_name="library",
        library_version="1.0",
        add_special_tokens=False,
    )
    baseline = chunk_config_identity_hash(config, descriptor)

    changed_descriptors = (
        replace(descriptor, model_name="changed-model"),
        replace(descriptor, revision="changed-revision"),
        replace(descriptor, library_name="changed-library"),
        replace(descriptor, library_version="2.0"),
        replace(descriptor, add_special_tokens=True),
    )

    assert all(
        chunk_config_identity_hash(config, changed_descriptor) != baseline
        for changed_descriptor in changed_descriptors
    )


def test_token_counter_protocol_describes_source_backed_offsets() -> None:
    """A structural counter supplies aligned token IDs and exact source offsets."""
    ports_module = import_module("omf_retrieval.application.indexing.ports")
    token_counter_type = getattr(ports_module, "TokenCounter", None)
    assert token_counter_type is not None
    assert callable(getattr(token_counter_type, "encode", None))
    assert callable(getattr(token_counter_type, "offsets", None))
    counter = _FakeTokenCounter()

    assert counter.encode("문서 A") == (47928, 49436, 32, 65)
    assert counter.offsets("문서 A") == ((0, 1), (1, 2), (2, 3), (3, 4))


def test_chunk_warning_is_stable_source_mapped_and_immutable() -> None:
    """An oversized atomic split warning keeps only stable safe metadata."""
    ports_module = import_module("omf_retrieval.application.indexing.ports")
    warning_type = getattr(ports_module, "ChunkWarning", None)
    assert warning_type is not None

    warning = warning_type(block_kind="table_row", line_start=7, line_end=9)

    assert warning.code == "oversized_atomic_unit_token_split"
    assert (warning.block_kind, warning.line_start, warning.line_end) == (
        "table_row",
        7,
        9,
    )
    assert not hasattr(warning, "__dict__")
    with pytest.raises(FrozenInstanceError):
        warning.line_end = 10


@pytest.mark.parametrize(
    "overrides",
    [
        {"code": "other"},
        {"code": _StrSubclass("oversized_atomic_unit_token_split")},
        {"block_kind": ""},
        {"block_kind": _StrSubclass("table_row")},
        {"line_start": True},
        {"line_start": _IntSubclass(7)},
        {"line_start": 0},
        {"line_end": 7.0},
        {"line_end": _IntSubclass(9)},
        {"line_end": 6},
    ],
)
def test_chunk_warning_rejects_unstable_or_invalid_metadata(
    overrides: dict[str, object],
) -> None:
    """Warning codes, kinds, and inclusive lines accept only exact values."""
    ports_module = import_module("omf_retrieval.application.indexing.ports")
    warning_type = getattr(ports_module, "ChunkWarning", None)
    assert warning_type is not None
    values = {"block_kind": "table_row", "line_start": 7, "line_end": 9}
    values.update(overrides)

    with pytest.raises(ValueError):
        warning_type(**values)


def test_chunk_draft_is_source_mapped_and_immutable() -> None:
    """A valid draft keeps deterministic coordinates and warning metadata."""
    ports_module = import_module("omf_retrieval.application.indexing.ports")
    warning_type = getattr(ports_module, "ChunkWarning", None)
    draft_type = getattr(ports_module, "ChunkDraft", None)
    assert warning_type is not None
    assert draft_type is not None
    warning = warning_type(block_kind="list_item", line_start=4, line_end=4)

    draft = draft_type(
        ordinal=0,
        raw_text="- 원문\n",
        search_text="제목\n- 원문",
        token_count=5,
        line_start=4,
        line_end=4,
        chunk_hash="a" * 64,
        warnings=(warning,),
    )

    assert draft.warnings == (warning,)
    assert draft.chunk_hash == "a" * 64
    assert not hasattr(draft, "__dict__")
    with pytest.raises(FrozenInstanceError):
        draft.ordinal = 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"ordinal": True},
        {"ordinal": _IntSubclass(0)},
        {"ordinal": -1},
        {"raw_text": ""},
        {"raw_text": _StrSubclass("원문")},
        {"search_text": ""},
        {"search_text": _StrSubclass("검색")},
        {"token_count": False},
        {"token_count": _IntSubclass(5)},
        {"token_count": 0},
        {"line_start": True},
        {"line_start": 0},
        {"line_end": 3},
        {"chunk_hash": "A" * 64},
        {"chunk_hash": _StrSubclass("a" * 64)},
        {"chunk_hash": "a" * 63},
        {"warnings": []},
        {"warnings": _TupleSubclass()},
        {"warnings": (object(),)},
    ],
)
def test_chunk_draft_rejects_invalid_exact_values(
    overrides: dict[str, object],
) -> None:
    """Draft identity, text, counts, lines, hash, and warnings are validated."""
    ports_module = import_module("omf_retrieval.application.indexing.ports")
    draft_type = getattr(ports_module, "ChunkDraft", None)
    assert draft_type is not None
    values = {
        "ordinal": 0,
        "raw_text": "원문",
        "search_text": "제목\n원문",
        "token_count": 5,
        "line_start": 4,
        "line_end": 4,
        "chunk_hash": "a" * 64,
        "warnings": (),
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        draft_type(**values)
