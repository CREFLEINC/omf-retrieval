from importlib.metadata import entry_points
from pathlib import Path

import typer

PROJECT_ROOT = Path(__file__).parents[2]
REQUIRED_PATHS = (
    "pyproject.toml",
    ".python-version",
    "README.md",
    "src/omf_retrieval/__init__.py",
    "src/omf_retrieval/domain/__init__.py",
    "src/omf_retrieval/application/__init__.py",
    "src/omf_retrieval/application/search/__init__.py",
    "src/omf_retrieval/application/indexing/__init__.py",
    "src/omf_retrieval/application/evaluation/__init__.py",
    "src/omf_retrieval/application/admin/__init__.py",
    "src/omf_retrieval/interfaces/__init__.py",
    "src/omf_retrieval/interfaces/api/__init__.py",
    "src/omf_retrieval/interfaces/api/routes/__init__.py",
    "src/omf_retrieval/interfaces/cli/__init__.py",
    "src/omf_retrieval/infrastructure/__init__.py",
    "src/omf_retrieval/infrastructure/database/__init__.py",
    "src/omf_retrieval/infrastructure/embedding/__init__.py",
    "src/omf_retrieval/infrastructure/source/__init__.py",
    "src/omf_retrieval/infrastructure/observability/__init__.py",
)


def test_project_structure_exists() -> None:
    missing = [path for path in REQUIRED_PATHS if not (PROJECT_ROOT / path).is_file()]

    assert missing == []


def test_package_exposes_version() -> None:
    import omf_retrieval

    assert getattr(omf_retrieval, "__version__", None) == "0.1.0"


def test_console_script_targets_typer_app() -> None:
    scripts = [
        entry_point
        for entry_point in entry_points(group="console_scripts")
        if entry_point.name == "omf-retrieval"
    ]

    assert [entry_point.value for entry_point in scripts] == [
        "omf_retrieval.interfaces.cli.main:app"
    ]

    app = scripts[0].load()
    assert isinstance(app, typer.Typer)
