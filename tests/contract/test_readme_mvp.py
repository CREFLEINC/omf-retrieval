"""Minimal local MVP handoff order and secret-handling contract."""

from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"


def test_readme_documents_only_the_required_local_mvp_sequence() -> None:
    text = README.read_text(encoding="utf-8")
    commands = (
        "docker compose -f compose.test.yaml up -d db",
        "uv run omf-retrieval model prepare",
        "uv run alembic upgrade head",
        'git -C "$OMF_SOURCE_REPOSITORY" worktree add --detach',
        "uv run omf-retrieval index",
        "uv run omf-retrieval client create",
        "uv run omf-retrieval serve",
        "uv run omf-retrieval search",
    )
    positions = [text.find(command) for command in commands]

    assert -1 not in positions
    assert positions == sorted(positions)
    calibration = text.find("uv run python scripts/calibrate_search.py")
    calibrated_reindex = text.find(
        "uv run omf-retrieval index", positions[4] + len("uv run omf-retrieval index")
    )
    assert positions[5] < calibration < calibrated_reindex < positions[6]
    assert "0.03658536400000001" in text
    assert "0.48344050397156374" in text
    assert "0.16857380984674064" in text
    assert "0.15203413442787495" not in text
    assert "OMF_RETRIEVAL_API_TOKEN" in text
    assert "--token" not in text
    assert "운영 배포" not in text
