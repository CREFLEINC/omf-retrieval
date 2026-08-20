"""Run deterministic Parent-Child chunking and ParentContext practice."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

from omf_retrieval.application.indexing.ports import (
    ChunkConfig,
    ChunkDraft,
    TokenizerDescriptor,
    split_physical_lines,
)
from omf_retrieval.infrastructure.source.chunker import (
    ParentChildChunker,
    ParentContextBuilder,
    chunk_config_identity_hash,
)
from omf_retrieval.infrastructure.source.markdown import MarkdownItParser

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE = PROJECT_ROOT / "tests/fixtures/markdown/long-section.md"
FULL_SHA = re.compile(r"[0-9a-f]{40}")


class CharacterTokenCounter:
    """Pedagogical counter: one Unicode code point is one source-backed token."""

    def encode(self, text: str) -> tuple[int, ...]:
        return tuple(ord(character) for character in text)

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((index, index + 1) for index in range(len(text)))


def _approved_source_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix != ".md"
    ):
        raise ValueError("--path must be a safe repository-relative Markdown path")
    parts = path.parts
    if parts[:2] not in {("docs", "research"), ("docs", "planning")} and (
        not parts or parts[0] != "uiux"
    ):
        raise ValueError("--path is outside the approved OMF include scope")
    return value


def _read_git_blob(repo: Path, commit: str, source_path: str) -> str:
    if FULL_SHA.fullmatch(commit) is None:
        raise ValueError("--commit must be a lowercase full 40-character SHA")
    source_path = _approved_source_path(source_path)
    completed = subprocess.run(
        ["git", "-C", str(repo.resolve()), "show", f"{commit}:{source_path}"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.decode("utf-8")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path, nargs="?", const=DEFAULT_FIXTURE)
    source.add_argument("--repo", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--path")
    parser.add_argument("--section", type=int, required=True)
    return parser.parse_args()


def _reconstruct_character_chunks(chunks: tuple[ChunkDraft, ...]) -> str:
    first, *remaining = chunks
    reconstructed = first.raw_text
    previous = first
    for chunk in remaining:
        overlap = min(64, len(previous.raw_text) - 1)
        if overlap:
            assert chunk.raw_text.startswith(previous.raw_text[-overlap:])
        reconstructed += chunk.raw_text[overlap:]
        previous = chunk
    return reconstructed


def main() -> None:
    args = _arguments()
    fixture_mode = args.repo is None
    if args.repo is not None:
        if args.commit is None or args.path is None:
            raise ValueError("--repo requires --commit and --path")
        source_path = _approved_source_path(args.path)
        source = _read_git_blob(args.repo, args.commit, source_path)
        provenance = {"mode": "git-show", "commit": args.commit, "path": source_path}
    else:
        if args.commit is not None or args.path is not None:
            raise ValueError("--commit/--path can only be used with --repo")
        fixture = args.fixture.resolve()
        source = fixture.read_text(encoding="utf-8")
        provenance = {"mode": "fixture", "path": str(fixture.relative_to(PROJECT_ROOT))}

    parser = MarkdownItParser()
    parsed = parser.parse(source)
    try:
        section = parsed.sections[args.section]
    except IndexError as error:
        raise ValueError("--section is outside the parsed section range") from error
    if not section.body.strip():
        raise ValueError("selected section has no chunkable body")

    counter = CharacterTokenCounter()
    config = ChunkConfig()
    descriptor = TokenizerDescriptor(
        model_name="practice/character-counter",
        revision="report-v1",
        library_name="python-builtin",
        library_version="3.12",
        add_special_tokens=False,
    )
    chunker = ParentChildChunker(counter, descriptor, config)
    chunks = chunker.split(section, parser_version=parsed.parser_version)
    repeated = chunker.split(section, parser_version=parsed.parser_version)
    assert chunks == repeated and chunks, "chunking must be deterministic and non-empty"
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.token_count <= config.atomic_max_tokens for chunk in chunks)
    source_lines = split_physical_lines(source)
    assert all(
        chunk.raw_text in "".join(source_lines[chunk.line_start - 1 : chunk.line_end])
        for chunk in chunks
    )
    if fixture_mode:
        assert _reconstruct_character_chunks(chunks) == section.body

    builder = ParentContextBuilder(parser, counter, config)
    selected = chunks[len(chunks) // 2]
    context = builder.build(
        section,
        matched_raw_text=selected.raw_text,
        matched_line_start=selected.line_start,
        matched_line_end=selected.line_end,
        parser_version=parsed.parser_version,
        matched_ordinal=selected.ordinal,
    )
    assert selected.raw_text in context.raw_text
    assert context.token_count <= config.parent_context_max_tokens
    assert context == builder.build(
        section,
        matched_raw_text=selected.raw_text,
        matched_line_start=selected.line_start,
        matched_line_end=selected.line_end,
        parser_version=parsed.parser_version,
        matched_ordinal=selected.ordinal,
    )

    result = {
        "provenance": provenance,
        "tokenizer_note": "practice only: one Unicode code point equals one token",
        "parser_version": parsed.parser_version,
        "section": {
            "ordinal": section.ordinal,
            "heading_path": list(section.heading_path),
            "lines": [section.line_start, section.line_end],
            "body_characters": len(section.body),
            "block_kinds": [block.kind for block in section.blocks],
        },
        "chunk_config": {
            "target_tokens": config.target_tokens,
            "soft_max_tokens": config.soft_max_tokens,
            "overlap_tokens": config.overlap_tokens,
            "atomic_max_tokens": config.atomic_max_tokens,
            "parent_context_max_tokens": config.parent_context_max_tokens,
        },
        "tokenizer_descriptor": {
            "model_name": descriptor.model_name,
            "revision": descriptor.revision,
            "library_name": descriptor.library_name,
            "library_version": descriptor.library_version,
            "add_special_tokens": descriptor.add_special_tokens,
        },
        "config_identity_hash": chunk_config_identity_hash(config, descriptor),
        "chunk_count": len(chunks),
        "chunks": [
            {
                "ordinal": chunk.ordinal,
                "lines": [chunk.line_start, chunk.line_end],
                "token_count": chunk.token_count,
                "raw_characters": len(chunk.raw_text),
                "search_prefix": " / ".join(section.heading_path),
                "chunk_hash": chunk.chunk_hash,
                "warnings": [warning.code for warning in chunk.warnings],
            }
            for chunk in chunks
        ],
        "selected_parent_context": {
            "matched_ordinal": selected.ordinal,
            "lines": [context.line_start, context.line_end],
            "token_count": context.token_count,
            "contains_exact_child": True,
        },
        "assertions": {
            "deterministic": True,
            "source_backed_line_ranges": True,
            "ordinal_sequence": True,
            "atomic_token_limit": True,
            "fixture_reconstruction": fixture_mode,
            "parent_context_limit_and_match": True,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
