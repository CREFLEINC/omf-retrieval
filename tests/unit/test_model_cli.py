"""CLI contract for fixed-revision model preparation."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from omf_retrieval.interfaces.cli import model as model_cli
from omf_retrieval.interfaces.cli.main import app


def test_root_help_exposes_model_command() -> None:
    assert any(group.name == "model" for group in app.registered_groups)


def test_root_and_model_help_are_side_effect_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        model_cli,
        "prepare_embedding_model",
        lambda settings: calls.append(settings),
    )
    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    model = runner.invoke(app, ["model", "--help"])
    prepare = runner.invoke(app, ["model", "prepare", "--help"])
    assert root.exit_code == model.exit_code == prepare.exit_code == 0
    assert "model" in root.stdout
    assert "prepare" in model.stdout
    assert calls == []


def test_prepare_prints_one_canonical_json_value_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = b'{"a":1,"b":"\xed\x95\x9c\xea\xb8\x80"}'
    settings_seen: list[object] = []

    def service(settings: object) -> bytes:
        settings_seen.append(settings)
        return output

    monkeypatch.setenv("OMF_RETRIEVAL_ENVIRONMENT", "test")
    monkeypatch.setenv("OMF_RETRIEVAL_EMBEDDING_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(model_cli, "prepare_embedding_model", service)
    result = CliRunner().invoke(app, ["model", "prepare"])
    assert result.exit_code == 0
    assert result.stdout == output.decode() + "\n"
    assert result.stderr == ""
    assert len(settings_seen) == 1
    assert json.loads(result.stdout) == {"a": 1, "b": "한글"}


@pytest.mark.parametrize(
    "failure",
    [OSError("secret-path"), ValueError("secret-value")],
)
def test_prepare_failure_is_nonzero_generic_and_has_no_partial_json(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def service(settings: object) -> bytes:
        raise failure

    monkeypatch.setattr(model_cli, "prepare_embedding_model", service)
    result = CliRunner().invoke(app, ["model", "prepare"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Embedding model preparation failed\n"
    assert "secret" not in result.stderr


@pytest.mark.parametrize("output", [b"not-json", b'{"b":2, "a":1}', b"\xff"])
def test_prepare_rejects_noncanonical_or_invalid_service_output(
    monkeypatch: pytest.MonkeyPatch, output: bytes
) -> None:
    monkeypatch.setattr(model_cli, "prepare_embedding_model", lambda settings: output)
    result = CliRunner().invoke(app, ["model", "prepare"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Embedding model preparation failed\n"
