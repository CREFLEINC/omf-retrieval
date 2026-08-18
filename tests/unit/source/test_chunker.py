"""Unit tests for deterministic chunking contracts and identities."""

import re
from dataclasses import FrozenInstanceError, replace
from importlib import import_module
from typing import Any

import pytest

from omf_retrieval.application.indexing.ports import (
    ChunkConfig,
    ParsedSection,
    TokenizerDescriptor,
)
from omf_retrieval.infrastructure.source.chunker import (
    CHUNKER_VERSION,
    chunk_config_identity_hash,
)
from omf_retrieval.infrastructure.source.markdown import MarkdownItParser


class _IntSubclass(int):
    """Exercise rejection of integer subclasses at contract boundaries."""


class _StrSubclass(str):
    """Exercise rejection of string subclasses at contract boundaries."""


class _TupleSubclass(tuple):
    """Exercise rejection of tuple subclasses at contract boundaries."""


class _ChunkConfigSubclass(ChunkConfig):
    """Represent an invalid config subtype at an exact DTO boundary."""


class _TokenizerDescriptorSubclass(TokenizerDescriptor):
    """Represent an invalid descriptor subtype at an exact DTO boundary."""


class _ParsedSectionSubclass(ParsedSection):
    """Represent an invalid section subtype at an exact DTO boundary."""


class _FakeTokenCounter:
    """Provide deterministic source-backed tokens for the protocol contract."""

    def encode(self, text: str) -> tuple[int, ...]:
        """Map each source character to one deterministic token."""
        return tuple(ord(character) for character in text)

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        """Map every fake token to its exact one-character source slice."""
        return tuple((index, index + 1) for index in range(len(text)))


class _PairTokenCounter:
    """Tokenize pairs so tests distinguish token offsets from character offsets."""

    def encode(self, text: str) -> tuple[int, ...]:
        """Return one stable token for each consecutive character pair."""
        return tuple(
            ord(text[index]) for index in range(0, len(text), 2) if text[index:]
        )

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        """Return exact half-open spans for each pair token."""
        return tuple(
            (index, min(index + 2, len(text))) for index in range(0, len(text), 2)
        )


class _EncodeResultCounter(_FakeTokenCounter):
    def __init__(self, result: object) -> None:
        self._result = result

    def encode(self, text: str) -> object:
        return self._result


class _OffsetResultCounter(_FakeTokenCounter):
    def __init__(self, result: object) -> None:
        self._result = result

    def offsets(self, text: str) -> object:
        return self._result


class _RaisingTokenCounter(_FakeTokenCounter):
    def encode(self, text: str) -> tuple[int, ...]:
        raise RuntimeError("secret-tokenizer-detail")


def _descriptor() -> TokenizerDescriptor:
    return TokenizerDescriptor(
        model_name="model",
        revision="revision",
        library_name="library",
        library_version="1.0",
        add_special_tokens=False,
    )


def _chunker(
    counter: object | None = None,
    *,
    config: ChunkConfig | None = None,
) -> Any:
    return _chunker_type()(
        counter if counter is not None else _FakeTokenCounter(),
        _descriptor(),
        config if config is not None else ChunkConfig(),
    )


def _chunker_type() -> Any:
    chunker_type = getattr(
        import_module("omf_retrieval.infrastructure.source.chunker"),
        "ParentChildChunker",
        None,
    )
    assert chunker_type is not None, "ParentChildChunker must be public"
    return chunker_type


def _section(source: str, *, ordinal: int = -1) -> ParsedSection:
    parsed = MarkdownItParser().parse(source)
    return parsed.sections[ordinal]


def test_parent_child_chunker_exposes_the_approved_public_split_api() -> None:
    """Removing the public splitter makes approved parser output unusable."""
    chunks = _chunker().split(_section("# A\nbody\n"), parser_version="parser-v1")

    assert len(chunks) == 1


@pytest.mark.parametrize("source", ["# Empty\n", "# Blank\n \t\r\n\n"])
def test_empty_or_blank_section_produces_no_children(source: str) -> None:
    """Heading-only and blank bodies must not create unsearchable children."""
    chunks = _chunker().split(_section(source), parser_version="parser-v1")

    assert chunks == ()


def test_search_text_at_exact_soft_max_remains_one_child_with_heading_path() -> None:
    """Splitting at the 600-token inclusive boundary would over-fragment text."""
    body = "x" * 596
    section = _section(f"# A\n## B\n{body}")

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert len(chunks) == 1
    assert chunks[0].raw_text == body
    assert chunks[0].search_text == f"A\nB\n{body}"
    assert chunks[0].token_count == 600
    assert (chunks[0].line_start, chunks[0].line_end) == (3, 3)


def test_long_normal_blocks_pack_to_target_without_exceeding_soft_max() -> None:
    """Ignoring block-aware target packing would fragment or oversize children."""
    first_paragraph = "a" * 350 + "\n\n"
    second_paragraph = "b" * 100 + "\n\n"
    third_paragraph = "c" * 300 + "\n"
    section = _section(f"# H\n{first_paragraph}{second_paragraph}{third_paragraph}")
    counter = _FakeTokenCounter()

    chunks = _chunker(counter).split(section, parser_version="parser-v1")

    assert len(chunks) == 2
    assert chunks[0].raw_text == first_paragraph + second_paragraph[:-1]
    assert chunks[0].token_count == 455
    assert chunks[1].raw_text == (chunks[0].raw_text[-64:] + "\n" + third_paragraph)
    assert chunks[1].raw_text.startswith(chunks[0].raw_text[-64:])
    assert (chunks[0].line_start, chunks[0].line_end) == (2, 4)
    assert (chunks[1].line_start, chunks[1].line_end) == (4, 6)
    assert all(
        chunk.token_count == len(counter.encode(chunk.search_text)) <= 600
        for chunk in chunks
    )


def test_oversized_normal_block_splits_at_token_offsets_with_exact_overlap() -> None:
    """Character slicing would break target size and the exact token overlap."""
    body = "ab" * 601
    section = _section(body)
    counter = _PairTokenCounter()

    chunks = _chunker(counter).split(section, parser_version="parser-v1")

    assert [chunk.token_count for chunk in chunks] == [400, 265]
    assert chunks[0].raw_text == body[:800]
    assert chunks[1].raw_text == body[672:]
    assert chunks[1].raw_text[:128] == chunks[0].raw_text[-128:]
    assert chunks[0].raw_text + chunks[1].raw_text[128:] == body
    assert all(chunk.token_count <= 600 for chunk in chunks)


@pytest.mark.parametrize(
    ("atomic_text", "expected_atomic", "expected_after"),
    [
        ("| h |\n|---|\n| v |\n", "| h |\n|---|\n| v |\n", "\n" + "z" * 300 + "\n"),
        ("- first\n- second\n", "- first\n- second\n\n", "z" * 300 + "\n"),
        ("> first\n>\n> second\n", "> first\n>\n> second\n", "\n" + "z" * 300 + "\n"),
    ],
    ids=["table", "list", "quote"],
)
def test_preserved_atomic_block_stays_separate_from_normal_text(
    atomic_text: str, expected_atomic: str, expected_after: str
) -> None:
    """Merging a small atomic block with normal text would lose its boundary."""
    before = "n" * 300 + "\n\n"
    after = "\n" + "z" * 300 + "\n"
    section = _section(f"# H\n{before}{atomic_text}{after}")

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert [chunk.raw_text for chunk in chunks] == [
        before,
        expected_atomic,
        expected_after,
    ]
    assert "".join(chunk.raw_text for chunk in chunks) == section.body
    assert chunks[1].warnings == ()
    assert chunks[1].token_count <= 800


@pytest.mark.parametrize(
    ("atomic_text", "expected_chunks"),
    [
        (
            "| " + "h" * 300 + " |\n|---|\n"
            "| " + "a" * 300 + " |\n"
            "| " + "b" * 300 + " |\n",
            (
                "| " + "h" * 300 + " |\n|---|\n| " + "a" * 300 + " |\n",
                "| " + "b" * 300 + " |\n",
            ),
        ),
        (
            "- " + "a" * 300 + "\n- " + "b" * 300 + "\n- " + "c" * 300 + "\n",
            (
                "- " + "a" * 300 + "\n- " + "b" * 300 + "\n",
                "- " + "c" * 300 + "\n",
            ),
        ),
        (
            "> " + "a" * 300 + "\n>\n> " + "b" * 300 + "\n>\n> " + "c" * 300 + "\n",
            (
                "> " + "a" * 300 + "\n>\n> " + "b" * 300 + "\n>\n",
                "> " + "c" * 300 + "\n",
            ),
        ),
    ],
    ids=["table-rows", "direct-list-items", "quote-children"],
)
def test_oversized_atomic_blocks_split_only_at_unit_boundaries(
    atomic_text: str, expected_chunks: tuple[str, ...]
) -> None:
    """Splitting preserved structures inside a fitting unit corrupts evidence."""
    section = _section(f"# H\n{atomic_text}")

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert tuple(chunk.raw_text for chunk in chunks) == expected_chunks
    assert "".join(chunk.raw_text for chunk in chunks) == atomic_text
    assert all(chunk.token_count <= 800 and not chunk.warnings for chunk in chunks)


@pytest.mark.parametrize(
    ("atomic_text", "warning_kind", "warning_line"),
    [
        ("- " + "x" * 900 + "\n", "list_item", 2),
        ("> " + "x" * 900 + "\n", "paragraph", 2),
        ("| h |\n|---|\n| " + "x" * 900 + " |\n", "table_row", 4),
    ],
    ids=["list-item", "quote-child", "table-row"],
)
def test_single_oversized_atomic_unit_uses_offsets_and_safe_warning_only(
    atomic_text: str,
    warning_kind: str,
    warning_line: int,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Silent token slicing or source-bearing logs would hide atomic damage."""
    section = _section(f"# H\n{atomic_text}")

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert "".join(chunk.raw_text for chunk in chunks) == atomic_text
    warning_chunks = tuple(chunk for chunk in chunks if chunk.warnings)
    assert len(warning_chunks) >= 2
    assert all(
        [
            (
                warning.code,
                warning.block_kind,
                warning.line_start,
                warning.line_end,
            )
            for warning in chunk.warnings
        ]
        == [
            (
                "oversized_atomic_unit_token_split",
                warning_kind,
                warning_line,
                warning_line,
            )
        ]
        for chunk in warning_chunks
    )
    assert all(chunk.token_count <= 800 for chunk in chunks)
    assert caplog.records == []
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_atomic_limit_is_inclusive_and_one_token_over_forces_warning_split() -> None:
    """Treating 800 as exclusive or permitting 801 breaks the approved boundary."""
    exact_raw = "- " + "x" * 795 + "\n"
    over_raw = "- " + "x" * 796 + "\n"

    exact_chunks = _chunker().split(
        _section(f"# H\n{exact_raw}"), parser_version="parser-v1"
    )
    over_chunks = _chunker().split(
        _section(f"# H\n{over_raw}"), parser_version="parser-v1"
    )

    assert len(exact_chunks) == 1
    assert exact_chunks[0].token_count == 800
    assert exact_chunks[0].warnings == ()
    assert [chunk.token_count for chunk in over_chunks] == [800, 3]
    assert "".join(chunk.raw_text for chunk in over_chunks) == over_raw
    assert all(chunk.warnings for chunk in over_chunks)


@pytest.mark.parametrize(
    "normal_block",
    [
        "```text\n" + "x" * 700 + "\n```\n",
        "<div>\n" + "x" * 700 + "\n</div>\n",
    ],
    ids=["fenced-code", "html"],
)
def test_fenced_code_and_html_use_normal_target_and_overlap(
    normal_block: str,
) -> None:
    """Misclassifying code or HTML as atomic would bypass normal overlap."""
    section = _section(f"# H\n{normal_block}")

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert len(chunks) == 2
    assert chunks[0].token_count == 400
    assert chunks[1].raw_text.startswith(chunks[0].raw_text[-64:])
    assert chunks[0].raw_text + chunks[1].raw_text[64:] == normal_block
    assert all(chunk.token_count <= 600 and not chunk.warnings for chunk in chunks)


def test_mixed_physical_endings_drive_actual_overlap_line_ranges() -> None:
    """Generic line splitting would miscount CRLF or Unicode separators."""
    unicode_separator = "\u2028"
    body = (
        "a" * 300
        + "\r\n"
        + "b" * 100
        + unicode_separator
        + "b" * 199
        + "\r"
        + "c" * 100
        + "\n"
    )
    section = _section(body)

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert [chunk.token_count for chunk in chunks] == [400, 368]
    assert [(chunk.line_start, chunk.line_end) for chunk in chunks] == [(1, 2), (2, 3)]
    assert chunks[1].raw_text.startswith(chunks[0].raw_text[-64:])
    assert chunks[0].raw_text + chunks[1].raw_text[64:] == body
    assert unicode_separator in chunks[1].raw_text


def test_duplicate_atomic_bodies_keep_lines_ordinals_and_distinct_hashes() -> None:
    """Locating excerpts by body text would map duplicates to the first source range."""
    table = "| h |\n|---|\n| " + "x" * 300 + " |\n"
    section = _section(f"# H\n{table}\nmiddle\n\n{table}")

    chunks = _chunker().split(section, parser_version="parser-v1")

    assert [chunk.ordinal for chunk in chunks] == [0, 1, 2]
    assert chunks[0].raw_text == chunks[2].raw_text == table
    assert (chunks[0].line_start, chunks[0].line_end) == (2, 4)
    assert chunks[1].raw_text == "\nmiddle\n\n"
    assert (chunks[2].line_start, chunks[2].line_end) == (8, 10)
    assert chunks[0].chunk_hash != chunks[2].chunk_hash
    assert "".join(chunk.raw_text for chunk in chunks) == section.body


def test_chunk_hash_matches_literal_task2_and_config_identity_coordinates() -> None:
    """Dropping any approved hash coordinate breaks deterministic chunk reuse."""
    chunk = _chunker().split(_section("# A\nbody\n"), parser_version="parser-v1")[0]

    assert chunk.chunk_hash == (
        "8e4e1be62615e60660bdfee6418d0ff52bdc4ea6b1977a880b71f99aaf84bcd1"
    )
    changed_parser_chunk = _chunker().split(
        _section("# A\nbody\n"), parser_version="parser-v2"
    )[0]
    assert changed_parser_chunk.chunk_hash != chunk.chunk_hash


def test_constructor_rejects_nonconcrete_contract_dtos_and_missing_methods() -> None:
    """DTO subtypes or incomplete tokenizers could alter deterministic identity."""
    chunker_type = _chunker_type()
    descriptor_values = {
        "model_name": "model",
        "revision": "revision",
        "library_name": "library",
        "library_version": "1.0",
        "add_special_tokens": False,
    }

    invalid_arguments = (
        (object(), _descriptor(), ChunkConfig()),
        (
            _FakeTokenCounter(),
            _TokenizerDescriptorSubclass(**descriptor_values),
            ChunkConfig(),
        ),
        (_FakeTokenCounter(), _descriptor(), _ChunkConfigSubclass()),
    )
    for token_counter, descriptor, config in invalid_arguments:
        with pytest.raises(ValueError, match="^Invalid chunker input contract$"):
            chunker_type(token_counter, descriptor, config)


@pytest.mark.parametrize("parser_version", [None, "", " \t", _StrSubclass("v1")])
def test_split_rejects_invalid_section_or_parser_identity(
    parser_version: object,
) -> None:
    """Invalid parser identity must fail before producing reusable chunk hashes."""
    section = _section("# A\nbody\n")

    with pytest.raises(ValueError, match="^Invalid split input contract$"):
        _chunker().split(section, parser_version=parser_version)


def test_split_rejects_parsed_section_subtypes() -> None:
    """A section subtype must not bypass the approved immutable parser DTO."""
    section = _section("# A\nbody\n")
    section_subtype = _ParsedSectionSubclass(
        ordinal=section.ordinal,
        parent_ordinal=section.parent_ordinal,
        level=section.level,
        heading=section.heading,
        heading_path=section.heading_path,
        body=section.body,
        line_start=section.line_start,
        line_end=section.line_end,
        blocks=section.blocks,
    )

    with pytest.raises(ValueError, match="^Invalid split input contract$"):
        _chunker().split(section_subtype, parser_version="parser-v1")


@pytest.mark.parametrize(
    "encode_result",
    ["secret-source-text", (True,), (1.5,), iter((1,))],
    ids=["string", "boolean-token", "float-token", "non-sequence"],
)
def test_malformed_encode_results_fail_closed_without_source(
    encode_result: object,
) -> None:
    """Malformed token IDs must not leak encoded source through an exception."""
    with pytest.raises(ValueError) as caught:
        _chunker(_EncodeResultCounter(encode_result)).split(
            _section("# Secret\nbody\n"), parser_version="parser-v1"
        )

    assert str(caught.value) == "Token counter returned malformed data"
    assert caught.value.__cause__ is None
    assert "Secret" not in str(caught.value)


def _invalid_offset_results(text_length: int) -> tuple[object, ...]:
    valid = [(index, index + 1) for index in range(text_length)]
    too_short = tuple(valid[:-1])
    zero_length = tuple([(0, 0), *valid[1:]])
    overlapping = tuple([valid[0], (0, 2), *valid[2:]])
    out_of_bounds = tuple([*valid[:-1], (text_length - 1, text_length + 1)])
    list_span = tuple([[0, 1], *valid[1:]])
    return too_short, zero_length, overlapping, out_of_bounds, list_span


@pytest.mark.parametrize(
    "offset_result",
    _invalid_offset_results(601),
    ids=["unaligned", "zero-length", "overlap", "out-of-bounds", "non-tuple-span"],
)
def test_malformed_offset_results_fail_closed_without_source(
    offset_result: object,
) -> None:
    """Unsafe source offsets must never reach slicing or expose source content."""
    with pytest.raises(ValueError) as caught:
        _chunker(_OffsetResultCounter(offset_result)).split(
            _section("s" * 601), parser_version="parser-v1"
        )

    assert str(caught.value) == "Token counter returned malformed data"
    assert caught.value.__cause__ is None
    assert "s" * 20 not in str(caught.value)


def test_token_counter_exceptions_are_sanitized_without_chaining() -> None:
    """Adapter exception details may contain source and must stay internal."""
    with pytest.raises(ValueError) as caught:
        _chunker(_RaisingTokenCounter()).split(
            _section("# Secret\nbody\n"), parser_version="parser-v1"
        )

    assert str(caught.value) == "Token counter failed"
    assert caught.value.__cause__ is None
    assert "secret-tokenizer-detail" not in str(caught.value)


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
