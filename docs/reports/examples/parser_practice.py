"""Read-only Markdown parser and occurrence-metadata practice."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from omf_retrieval.application.indexing.metadata import extract_metadata
from omf_retrieval.application.indexing.ports import ParsedBlock, split_physical_lines
from omf_retrieval.infrastructure.source.markdown import MarkdownItParser

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE = PROJECT_ROOT / "tests/fixtures/markdown/block-structure.md"
FULL_SHA = re.compile(r"[0-9a-f]{40}")


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
    if parts[:2] in {("docs", "raw"), ("docs", "_workspace")}:
        raise ValueError("--path is excluded from the OMF source profile")
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


def _block_view(block: ParsedBlock) -> dict[str, object]:
    return {
        "kind": block.kind,
        "lines": [block.line_start, block.line_end],
        "characters": len(block.raw_text),
        "children": [_block_view(child) for child in block.children],
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path, nargs="?", const=DEFAULT_FIXTURE)
    source.add_argument("--repo", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--path")
    parser.add_argument("--max-sections", type=int, default=6)
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    if args.repo is not None:
        if args.commit is None or args.path is None:
            raise ValueError("--repo requires --commit and --path")
        source_path = _approved_source_path(args.path)
        source = _read_git_blob(args.repo, args.commit, source_path)
        provenance = {"mode": "git-show", "commit": args.commit, "path": source_path}
        metadata = extract_metadata(source_path, split_physical_lines(source))
    else:
        if args.commit is not None or args.path is not None:
            raise ValueError("--commit/--path can only be used with --repo")
        fixture = args.fixture.resolve()
        source = fixture.read_text(encoding="utf-8")
        provenance = {"mode": "fixture", "path": str(fixture.relative_to(PROJECT_ROOT))}
        metadata = None

    parser = MarkdownItParser()
    parsed = parser.parse(source)
    assert parsed == parser.parse(source), "parsing must be deterministic"
    assert [section.ordinal for section in parsed.sections] == list(
        range(len(parsed.sections))
    )
    for section in parsed.sections:
        assert "".join(block.raw_text for block in section.blocks) == section.body

    result = {
        "provenance": provenance,
        "parser_version": parsed.parser_version,
        "physical_line_count": len(split_physical_lines(source)),
        "metadata": (
            None
            if metadata is None
            else {
                **asdict(metadata),
                "document_date": (
                    metadata.document_date.isoformat()
                    if metadata.document_date is not None
                    else None
                ),
                "version_scope": metadata.version_scope.value,
                "decision_state": metadata.decision_state.value,
                "owner_domain": metadata.owner_domain.value,
            }
        ),
        "section_count": len(parsed.sections),
        "sections": [
            {
                "ordinal": section.ordinal,
                "parent_ordinal": section.parent_ordinal,
                "level": section.level,
                "heading": section.heading,
                "heading_path": list(section.heading_path),
                "lines": [section.line_start, section.line_end],
                "body_characters": len(section.body),
                "blocks": [_block_view(block) for block in section.blocks],
            }
            for section in parsed.sections[: args.max_sections]
        ],
        "assertions": {
            "deterministic": True,
            "sequential_ordinals": True,
            "every_section_body_reconstructed_from_blocks": True,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
