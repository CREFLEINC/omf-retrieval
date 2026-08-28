"""Production Docker Compose packaging contract."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.production.yaml"
PGVECTOR_DIGEST = (
    "pgvector/pgvector@"
    "sha256:1963bc48febf543433baa1ce3edcc6cc08154de722e22495f86681cc9a849026"
)
REQUIRED_ENVIRONMENT = {
    "OMF_APP_UID",
    "OMF_APP_GID",
    "OMF_POSTGRES_DATA_DIR",
    "OMF_MODEL_CACHE_DIR",
    "OMF_SOURCE_REPO_DIR",
    "OMF_POSTGRES_PASSWORD_FILE",
    "OMF_AUDIT_HMAC_KEY_FILE",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "OMF_RETRIEVAL_DATABASE_URL",
}


def test_production_deployment_assets_exist() -> None:
    """Require every repository-owned production deployment asset."""
    expected = (
        "Dockerfile",
        "compose.production.yaml",
        ".dockerignore",
        ".env.example",
    )

    missing = [path for path in expected if not (ROOT / path).is_file()]

    assert missing == []


def _write_render_environment(tmp_path: Path) -> Path:
    env_file = tmp_path / "production.env"
    values = {
        "OMF_APP_UID": "12345",
        "OMF_APP_GID": "12345",
        "OMF_POSTGRES_DATA_DIR": str(tmp_path / "postgres"),
        "OMF_MODEL_CACHE_DIR": str(tmp_path / "model-cache"),
        "OMF_SOURCE_REPO_DIR": str(tmp_path / "source" / "omf"),
        "OMF_POSTGRES_PASSWORD_FILE": str(tmp_path / "postgres_password"),
        "OMF_AUDIT_HMAC_KEY_FILE": str(tmp_path / "audit_hmac_key"),
        "POSTGRES_DB": "omf_retrieval",
        "POSTGRES_USER": "omf_retrieval",
        "OMF_RETRIEVAL_DATABASE_URL": (
            "postgresql+psycopg://omf_retrieval:fixture-value@postgres:5432/"
            "omf_retrieval"
        ),
    }
    env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    return env_file


def _compose_config(env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "omf-retrieval-contract",
            "--env-file",
            str(env_file),
            "-f",
            str(COMPOSE),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env={"PATH": os.environ["PATH"]},
        check=False,
        capture_output=True,
        text=True,
    )


def _volume(service: dict[str, object], target: str) -> dict[str, object]:
    volumes = service["volumes"]
    assert isinstance(volumes, list)
    return next(volume for volume in volumes if volume["target"] == target)


def test_production_compose_renders_the_approved_runtime(tmp_path: Path) -> None:
    result = _compose_config(_write_render_environment(tmp_path))

    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    services = rendered["services"]
    assert set(services) == {"api", "postgres"}

    database = services["postgres"]
    assert database["image"] == PGVECTOR_DIGEST
    assert database["platform"] == "linux/amd64"
    assert database["healthcheck"]["test"][0] == "CMD-SHELL"
    assert database["healthcheck"]["test"][1].startswith("pg_isready ")
    assert database["environment"]["POSTGRES_PASSWORD_FILE"] == (
        "/run/omf-retrieval/secrets/postgres_password"
    )
    database_data = _volume(database, "/var/lib/postgresql/data")
    database_password = _volume(
        database, "/run/omf-retrieval/secrets/postgres_password"
    )
    assert database_data["source"] == str(tmp_path / "postgres")
    assert database_data.get("read_only", False) is False
    assert database_password["source"] == str(tmp_path / "postgres_password")
    assert database_password["read_only"] is True

    api = services["api"]
    assert api["platform"] == "linux/amd64"
    assert api["restart"] == "unless-stopped"
    assert api["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert api["environment"]["OMF_RETRIEVAL_ENVIRONMENT"] == "production"
    assert api["environment"]["OMF_RETRIEVAL_EMBEDDING_DEVICE"] == "cuda:0"
    assert api["command"] == [
        "omf-retrieval",
        "serve",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    assert api["healthcheck"]["test"][0:2] == ["CMD", "python"]
    assert api["deploy"]["resources"]["reservations"]["devices"] == [
        {"capabilities": ["gpu"], "device_ids": ["0"], "driver": "nvidia"}
    ]
    assert api["ports"] == [
        {
            "host_ip": "192.168.1.185",
            "mode": "ingress",
            "protocol": "tcp",
            "published": "9090",
            "target": 8000,
        }
    ]
    source = _volume(api, "/srv/omf-source")
    model_cache = _volume(api, "/var/cache/omf-retrieval/models")
    api_password = _volume(api, "/run/omf-retrieval/secrets/postgres_password")
    audit_key = _volume(api, "/run/omf-retrieval/secrets/audit_hmac_key")
    assert source["source"] == str(tmp_path / "source" / "omf")
    assert source["read_only"] is True
    assert model_cache["source"] == str(tmp_path / "model-cache")
    assert model_cache.get("read_only", False) is False
    assert api_password["source"] == str(tmp_path / "postgres_password")
    assert api_password["read_only"] is True
    assert audit_key["source"] == str(tmp_path / "audit_hmac_key")
    assert audit_key["read_only"] is True


def test_production_compose_requires_every_host_value_and_fails_safely(
    tmp_path: Path,
) -> None:
    source = COMPOSE.read_text(encoding="utf-8")
    for name in REQUIRED_ENVIRONMENT:
        assert f"${{{name}:?" in source

    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    result = _compose_config(empty_env)

    assert result.returncode != 0
    assert "required" in result.stderr.lower()
    assert "fixture-value" not in result.stderr


def test_dockerfile_uses_locked_python_runtime_with_git_and_non_root_user() -> None:
    source = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12.12-slim-bookworm" in source
    assert "uv sync --frozen --no-dev" in source
    assert "apt-get install" in source and "git" in source
    assert "ARG APP_UID" in source and "ARG APP_GID" in source
    assert "USER omf-retrieval" in source
    assert 'ENV PATH="/app/.venv/bin:$PATH"' in source
    assert 'CMD ["omf-retrieval", "serve"' in source


def test_repository_and_image_context_exclude_private_runtime_material() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert ".env" in gitignore
    assert "secrets/" in gitignore
    for pattern in (
        ".git",
        ".venv",
        ".env",
        "secrets/",
        "**/__pycache__/",
        "*.py[cod]",
        "*.zip",
        "postgres/",
        "model-cache/",
        "source/",
    ):
        assert pattern in dockerignore


def test_new_deployment_assets_have_no_forbidden_host_or_gpu_shortcuts() -> None:
    source = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "Dockerfile",
            "compose.production.yaml",
            ".dockerignore",
            ".env.example",
        )
    )

    assert "/Users/" not in source
    assert "count: all" not in source
    assert "device_ids: [all]" not in source
    assert "0.8.6-pg18-trixie@sha256" not in source
    assert source.count("9090") == 2


def test_readme_records_metadata_and_the_approved_server_sequence() -> None:
    source = (ROOT / "README.md").read_text(encoding="utf-8")
    deployment = source.split("## Production Docker Compose", maxsplit=1)[1]
    prerequisite_heading = "### 소스 사전 준비"
    runtime_heading = "### 런타임 초기화 및 기동"
    assert deployment.find(prerequisite_heading) < deployment.find(runtime_heading)
    prerequisite, runtime = deployment.split(runtime_heading, maxsplit=1)
    prerequisite_commands = (
        "git clone '<OMF_PRIVATE_GIT_URL>'",
        "checkout --detach a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2",
        "status --porcelain",
    )
    runtime_commands = (
        "up -d postgres",
        "alembic upgrade head",
        "omf-retrieval model prepare",
        "omf-retrieval index",
        "omf-retrieval client create",
        "up -d api",
        "omf-retrieval search",
    )
    prerequisite_positions = [
        prerequisite.find(command) for command in prerequisite_commands
    ]
    runtime_positions = [runtime.find(command) for command in runtime_commands]

    assert -1 not in prerequisite_positions
    assert prerequisite_positions == sorted(prerequisite_positions)
    assert -1 not in runtime_positions
    assert runtime_positions == sorted(runtime_positions)
    assert "git clone" not in runtime
    assert "Codex — 사용자 승인 반영" in source
    assert "v2.0" in source
    assert "프로젝트 관련자" in source
    assert "/opt/omf-retrieval" in source
    assert "/home/storage_disk3/omf-retrieval-disk" in source
    assert 'test -z "$(git -C "$OMF_SOURCE_REPO_DIR" status --porcelain)"' in source
    assert "chmod 600 .env" in source
    assert "secrets/postgres_password" in source
    assert "secrets/audit_hmac_key" in source
    assert "같아야" in source
