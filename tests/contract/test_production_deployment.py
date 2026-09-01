"""Production Docker Compose packaging contract."""

import json
import os
import re
import shlex
import signal
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
    "OMF_RETRIEVAL_KEYWORD_SIMILARITY_FLOOR",
    "OMF_RETRIEVAL_VECTOR_SIMILARITY_FLOOR",
    "OMF_RETRIEVAL_EVIDENCE_FLOOR_STATUS",
}
CALIBRATED_SEARCH_POLICY = {
    "OMF_RETRIEVAL_KEYWORD_SIMILARITY_FLOOR": "0.012345678901234567",
    "OMF_RETRIEVAL_VECTOR_SIMILARITY_FLOOR": "0.567890123456789",
    "OMF_RETRIEVAL_EVIDENCE_FLOOR_STATUS": "calibrated",
}

TOKEN_FILE_PLACEHOLDER = "DEPLOYMENT_TOKEN_FILE='<preflight-confirmed-token-file>'"
TOKEN_OWNER_PLACEHOLDER = "DEPLOYMENT_TOKEN_OWNER_UID='<preflight-confirmed-owner-uid>'"


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
        **CALIBRATED_SEARCH_POLICY,
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
    assert "/var/lib/postgresql/data" not in COMPOSE.read_text(encoding="utf-8")
    rendered = json.loads(result.stdout)
    services = rendered["services"]
    assert set(services) == {"api", "postgres"}

    database = services["postgres"]
    database_targets = {volume["target"] for volume in database["volumes"]}
    assert "/var/lib/postgresql/data" not in database_targets
    assert database["image"] == PGVECTOR_DIGEST
    assert database["platform"] == "linux/amd64"
    assert database["healthcheck"]["test"][0] == "CMD-SHELL"
    assert database["healthcheck"]["test"][1].startswith("pg_isready ")
    assert database["environment"]["POSTGRES_PASSWORD_FILE"] == (
        "/run/omf-retrieval/secrets/postgres_password"
    )
    database_data = _volume(database, "/var/lib/postgresql")
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
    for name, value in CALIBRATED_SEARCH_POLICY.items():
        assert api["environment"][name] == value
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


def test_production_search_policy_is_explicit_and_pending_until_cuda_calibration() -> (
    None
):
    """Never start production with hidden CPU floors or an unmarked policy."""
    compose = COMPOSE.read_text(encoding="utf-8")
    for name in CALIBRATED_SEARCH_POLICY:
        required_interpolation = f"${{{name}:?{name} is required}}"
        assert compose.count(required_interpolation) == 1

    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    example_lines = set(example.splitlines())
    assert "CUDA raw calibration" in example
    assert "OMF_RETRIEVAL_KEYWORD_SIMILARITY_FLOOR=0.0" in example_lines
    assert "OMF_RETRIEVAL_VECTOR_SIMILARITY_FLOOR=0.0" in example_lines
    assert "OMF_RETRIEVAL_EVIDENCE_FLOOR_STATUS=calibration_pending" in example_lines
    assert "set OMF_RETRIEVAL_EVIDENCE_FLOOR_STATUS=calibrated" in example


def test_dockerfile_uses_locked_python_runtime_with_git_and_non_root_user() -> None:
    source = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12.12-slim-bookworm" in source
    assert "uv sync --frozen --no-dev" in source
    assert "apt-get install" in source and "git" in source
    assert "ARG APP_UID" in source and "ARG APP_GID" in source
    assert "USER omf-retrieval" in source
    assert 'ENV PATH="/app/.venv/bin:$PATH"' in source
    assert 'CMD ["omf-retrieval", "serve"' in source


def test_omf_profile_is_tracked_and_not_explicitly_ignored() -> None:
    """Static safeguards keep the runtime profile in the Docker context."""
    profile_relative_path = Path("config/source_profiles/omf.json")
    profile = ROOT / profile_relative_path

    assert profile.is_file()
    assert not profile.is_symlink()
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", profile_relative_path.as_posix()],
        cwd=ROOT,
        env={"PATH": os.environ["PATH"]},
        check=False,
        capture_output=True,
        text=True,
    )
    assert tracked.returncode == 0, tracked.stderr

    dockerignore_patterns = {
        line.strip().removeprefix("/")
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert dockerignore_patterns.isdisjoint(
        {
            "config",
            "config/",
            "config/*",
            "config/**",
            "config/source_profiles",
            "config/source_profiles/",
            "config/source_profiles/*",
            "config/source_profiles/**",
            profile_relative_path.as_posix(),
        }
    )


def test_dockerfile_packages_and_checks_the_omf_source_profile() -> None:
    """The image build fails closed before installing an unreadable profile."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerfile_lines = dockerfile.splitlines()
    copy_command = (
        "COPY config/source_profiles/omf.json ./config/source_profiles/omf.json"
    )
    readability_check = "RUN test -r config/source_profiles/omf.json \\"
    install_command = "    && uv sync --frozen --no-dev --no-editable \\"
    chown_command = "    && chown -R omf-retrieval:omf-retrieval /app"
    assert dockerfile_lines.count(copy_command) == 1
    assert dockerfile_lines.count(readability_check) == 1
    assert (
        dockerfile_lines.index("WORKDIR /app")
        < dockerfile_lines.index(copy_command)
        < dockerfile_lines.index(readability_check)
        < dockerfile_lines.index(install_command)
        < dockerfile_lines.index(chown_command)
        < dockerfile_lines.index("USER omf-retrieval")
    )


def test_dockerfile_packages_the_cuda_calibration_entrypoint_and_smoke_fixture() -> (
    None
):
    """The production image carries only the inputs needed for raw calibration."""
    dockerfile_lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    required_copies = {
        "scripts/calibrate_search.py": (
            "COPY scripts/calibrate_search.py ./scripts/calibrate_search.py"
        ),
        "config/smoke/omf_mvp_v2.json": (
            "COPY config/smoke/omf_mvp_v2.json ./config/smoke/omf_mvp_v2.json"
        ),
    }

    for source_path, copy_command in required_copies.items():
        assert (ROOT / source_path).is_file()
        assert dockerfile_lines.count(copy_command) == 1
        assert dockerfile_lines.index(copy_command) < dockerfile_lines.index(
            "USER omf-retrieval"
        )


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


def test_docker_context_excludes_web_artifacts_but_keeps_frontend_sources() -> None:
    """Keep local web build artifacts out without excluding build inputs."""
    dockerignore_patterns = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        "/web/node_modules/",
        "/web/dist/",
        "/web/.cache/",
        "/.pnpm-store/",
    } <= dockerignore_patterns
    frontend_sources = (
        "web/package.json",
        "web/pnpm-lock.yaml",
        "web/pnpm-workspace.yaml",
        "web/index.html",
        "web/tsconfig.json",
        "web/tsconfig.app.json",
        "web/tsconfig.node.json",
        "web/vite.config.ts",
        "web/src",
    )
    ignored_frontend_sources = {
        pattern
        for source_path in frontend_sources
        for pattern in (source_path, f"/{source_path}")
    }
    ignored_frontend_sources.update(
        {
            "web",
            "web/",
            "web/*",
            "web/**",
            "/web",
            "/web/",
            "/web/*",
            "/web/**",
            "web/src/",
            "web/src/**",
            "/web/src/",
            "/web/src/**",
        }
    )
    assert dockerignore_patterns.isdisjoint(ignored_frontend_sources)

    for source_path in frontend_sources:
        assert (ROOT / source_path).exists()

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for copy_command in (
        "COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./",
        (
            "COPY web/index.html web/tsconfig.json web/tsconfig.app.json "
            "web/tsconfig.node.json ./"
        ),
        "COPY web/vite.config.ts ./",
        "COPY web/src ./src",
    ):
        assert copy_command in dockerfile


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
    harness_path = Path(
        ".agents/skills/development-workflow/references/deployment-harness.md"
    )
    assert f"[공유 배포 하네스]({harness_path.as_posix()})" in deployment
    assert (ROOT / harness_path).is_file()
    redeploy_heading = "### 기존 공유 환경 재배포"
    bootstrap_heading = "### 최초 bootstrap"
    assert redeploy_heading in deployment
    assert bootstrap_heading in deployment
    assert deployment.find(redeploy_heading) < deployment.find(bootstrap_heading)
    redeploy, bootstrap = deployment.split(redeploy_heading, maxsplit=1)[1].split(
        bootstrap_heading, maxsplit=1
    )

    for forbidden_redeploy_command in (
        "omf-retrieval index",
        "omf-retrieval client create",
    ):
        assert forbidden_redeploy_command not in redeploy
        assert forbidden_redeploy_command in bootstrap

    redeploy_sequence = (
        "alembic upgrade head",
        "scripts/calibrate_search.py",
        "OMF_RETRIEVAL_KEYWORD_SIMILARITY_FLOOR",
        "OMF_RETRIEVAL_VECTOR_SIMILARITY_FLOOR",
        "OMF_RETRIEVAL_EVIDENCE_FLOOR_STATUS",
        "내부 readiness",
        "6개 smoke",
        "publish",
    )
    positions = [redeploy.find(term) for term in redeploy_sequence]
    assert -1 not in positions
    assert positions == sorted(positions)

    for preserved in (
        "427f2c4a-ab06-486a-9801-4bde3ef17d63",
        "5,584",
        "기존 deployment token",
        "기존 model cache",
    ):
        assert preserved in redeploy
    assert "별도 승인" in bootstrap
    assert "기존 model cache" in bootstrap
    assert "기존 deployment token" in bootstrap
    assert "Codex — 사용자 승인 반영" in source
    assert "v2.1" in deployment
    assert "프로젝트 관련자" in source
    assert "/opt/omf-retrieval" in source
    assert "/home/storage_disk3/omf-retrieval-disk" in source
    assert "chmod 600 .env" in source
    assert "secrets/postgres_password" in source
    assert "secrets/audit_hmac_key" in source
    assert "server preflight/build/migration/CUDA calibration/smoke" in deployment
    assert "NOT RUN" in deployment
    assert "외부 listener와 API는 중단" in deployment
    assert "publish 금지" in deployment

    for secret_value_shape in (
        "omfr_",
        "Bearer ",
        "postgresql+psycopg://",
    ):
        assert secret_value_shape not in deployment


def _readme_calibration_shell() -> tuple[str, str]:
    source = (ROOT / "README.md").read_text(encoding="utf-8")
    production = source.split("## Production Docker Compose", maxsplit=1)[1]
    redeploy = production.split("### 기존 공유 환경 재배포", maxsplit=1)[1].split(
        "### 최초 bootstrap", maxsplit=1
    )[0]
    match = re.search(
        r"4\. .*?deployment token.*?```bash\n(?P<shell>.*?)\n\s*```",
        redeploy,
        re.DOTALL,
    )
    assert match is not None
    return redeploy, match.group("shell")


def test_readme_bounds_calibration_token_to_the_compose_command() -> None:
    """A deployment token must never enter the parent host-shell environment."""
    redeploy, shell = _readme_calibration_shell()

    assert TOKEN_FILE_PLACEHOLDER in shell
    assert TOKEN_OWNER_PLACEHOLDER in shell
    assert "stat -c '%a' -- \"$DEPLOYMENT_TOKEN_FILE\"" in shell
    assert "stat -c '%u' -- \"$DEPLOYMENT_TOKEN_FILE\"" in shell
    for explicit_guard in (
        'test -n "${DEPLOYMENT_TOKEN_FILE-}" || exit 64',
        'test -n "${DEPLOYMENT_TOKEN_OWNER_UID-}" || exit 64',
        'test -f "$DEPLOYMENT_TOKEN_FILE" || exit 64',
        'test ! -L "$DEPLOYMENT_TOKEN_FILE" || exit 64',
        "test \"$(stat -c '%a' -- \"$DEPLOYMENT_TOKEN_FILE\")\" = '600' || exit 64",
        'test "$(stat -c \'%u\' -- "$DEPLOYMENT_TOKEN_FILE")" = '
        '"$DEPLOYMENT_TOKEN_OWNER_UID" || exit 64',
        'OMF_RETRIEVAL_API_TOKEN="$(<"$DEPLOYMENT_TOKEN_FILE")" || exit 64',
        'test -n "$OMF_RETRIEVAL_API_TOKEN" || exit 64',
    ):
        assert explicit_guard in shell

    subshell_match = re.fullmatch(r"\s*\(\n(?P<body>.*)\n\s*\)\s*", shell, re.DOTALL)
    assert subshell_match is not None
    body = subshell_match.group("body")
    assert "set -u" in body
    assert "set -e" not in body
    assert "set +x" in body
    assert "|| exit 64" in body
    assert 'OMF_RETRIEVAL_API_TOKEN="$(<"$DEPLOYMENT_TOKEN_FILE")"' in body
    assert "export OMF_RETRIEVAL_API_TOKEN" in body
    assert "exec docker compose" in body
    assert "-e OMF_RETRIEVAL_API_TOKEN" in body
    assert "-e OMF_RETRIEVAL_API_TOKEN=" not in body
    assert body.rstrip().endswith("api python scripts/calibrate_search.py")

    syntax = subprocess.run(
        ["bash", "-n"],
        input=shell,
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr
    for bounded_lifetime in ("정상 종료", "명령 실패", "interrupt", "parent shell"):
        assert bounded_lifetime in redeploy
    assert "compose exit status" in redeploy
    assert "암묵적으로 상속하지" in " ".join(redeploy.split())


def _write_fake_command(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _render_calibration_shell(
    shell: str,
    *,
    token_file: Path | None,
    owner_uid: str,
) -> str:
    if token_file is None:
        rendered = shell.replace(f"     {TOKEN_FILE_PLACEHOLDER}\n", "")
    else:
        rendered = shell.replace(
            TOKEN_FILE_PLACEHOLDER,
            f"DEPLOYMENT_TOKEN_FILE={shlex.quote(str(token_file))}",
        )
    return rendered.replace(
        TOKEN_OWNER_PLACEHOLDER,
        f"DEPLOYMENT_TOKEN_OWNER_UID={shlex.quote(owner_uid)}",
    )


def test_calibration_shell_fails_closed_without_a_docker_daemon(tmp_path: Path) -> None:
    """Exercise the Ubuntu/GNU-stat contract with portable fake commands."""
    _, shell = _readme_calibration_shell()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(
        fake_bin / "stat",
        """#!/usr/bin/env bash
if [[ "$#" -ne 4 || "$1" != '-c' || "$3" != '--' ]]; then
  exit 64
fi
case "$2" in
  '%a') printf '%s\\n' "${FAKE_TOKEN_MODE:?}" ;;
  '%u') printf '%s\\n' "${FAKE_TOKEN_OWNER_UID:?}" ;;
  *) exit 64 ;;
esac
""",
    )
    _write_fake_command(
        fake_bin / "docker",
        """#!/usr/bin/env bash
: > "${FAKE_DOCKER_MARKER:?}"
if [[ -n "${FAKE_DOCKER_SIGNAL:-}" ]]; then
  kill "-${FAKE_DOCKER_SIGNAL}" "$$"
fi
exit "${FAKE_DOCKER_EXIT:-0}"
""",
    )

    token_file = tmp_path / "deployment-token"
    token_file.write_text("ephemeral-fixture-token", encoding="utf-8")
    empty_token_file = tmp_path / "empty-token"
    empty_token_file.write_text("", encoding="utf-8")
    symlink_token_file = tmp_path / "symlink-token"
    symlink_token_file.symlink_to(token_file)
    directory_token_file = tmp_path / "token-directory"
    directory_token_file.mkdir()
    missing_token_file = tmp_path / "missing-token"

    def run_case(
        name: str,
        *,
        path: Path | None,
        mode: str = "600",
        actual_owner_uid: str = "1000",
        expected_owner_uid: str = "1000",
        docker_exit: int = 0,
        docker_signal: str = "",
        context: str = "top-level",
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
        docker_marker = tmp_path / f"docker-called-{name}"
        success_marker = tmp_path / f"calibration-succeeded-{name}"
        handler_marker = tmp_path / f"failure-handler-called-{name}"
        environment = {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_TOKEN_MODE": mode,
            "FAKE_TOKEN_OWNER_UID": actual_owner_uid,
            "FAKE_DOCKER_MARKER": str(docker_marker),
            "FAKE_DOCKER_EXIT": str(docker_exit),
            "FAKE_DOCKER_SIGNAL": docker_signal,
        }
        rendered = _render_calibration_shell(
            shell,
            token_file=path,
            owner_uid=expected_owner_uid,
        )
        if context == "or-handler":
            command = f"""(
{rendered}
) || {{
  : > {shlex.quote(str(handler_marker))}
  exit 0
}}
: > {shlex.quote(str(success_marker))}
"""
        elif context == "if-condition":
            command = f"""if (
{rendered}
); then
  : > {shlex.quote(str(success_marker))}
else
  : > {shlex.quote(str(handler_marker))}
fi
"""
        else:
            assert context == "top-level"
            command = rendered
        result = subprocess.run(
            ["bash", "-c", command],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        return result, docker_marker, success_marker, handler_marker

    success, success_marker, _, _ = run_case("success", path=token_file)
    assert success.returncode == 0, success.stderr
    assert success_marker.is_file()

    invalid_cases = (
        ("wrong-mode", token_file, {"mode": "644"}),
        (
            "owner-mismatch",
            token_file,
            {"actual_owner_uid": "2000"},
        ),
        ("symlink", symlink_token_file, {}),
        ("empty", empty_token_file, {}),
        ("missing", missing_token_file, {}),
        ("unset-path", None, {}),
        ("not-regular", directory_token_file, {}),
    )
    for name, path, overrides in invalid_cases:
        result, marker, _, _ = run_case(name, path=path, **overrides)
        assert result.returncode != 0, name
        assert not marker.exists(), name

    for context in ("or-handler", "if-condition"):
        for name, path, overrides in invalid_cases:
            result, docker_marker, success_marker, handler_marker = run_case(
                f"{context}-{name}",
                path=path,
                context=context,
                **overrides,
            )
            assert result.returncode == 0, (context, name, result.stderr)
            assert not docker_marker.exists(), (context, name)
            assert not success_marker.exists(), (context, name)
            assert handler_marker.is_file(), (context, name)

    compose_failure, compose_failure_marker, _, _ = run_case(
        "compose-failure",
        path=token_file,
        docker_exit=37,
    )
    assert compose_failure.returncode == 37
    assert compose_failure_marker.is_file()

    compose_interrupt, compose_interrupt_marker, _, _ = run_case(
        "compose-interrupt",
        path=token_file,
        docker_signal="TERM",
    )
    assert compose_interrupt.returncode == 128 + signal.SIGTERM
    assert compose_interrupt_marker.is_file()
