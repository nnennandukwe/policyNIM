"""Package metadata checks for direct CLI installs."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _project() -> dict[str, Any]:
    """Load pyproject data for package release contract assertions."""
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_runtime_dependencies_pin_sqlite_vec_for_portable_local_index() -> None:
    """Keep the local index backend compatible with standalone release targets."""
    dependencies = set(_project()["project"]["dependencies"])

    assert "sqlite-vec==0.1.9" in dependencies


def test_runtime_dependencies_do_not_ship_lancedb_backend() -> None:
    """Prevent standalone releases from depending on missing macOS x86_64 wheels."""
    dependencies = {
        dependency.split("==", maxsplit=1)[0].lower()
        for dependency in _project()["project"]["dependencies"]
    }

    assert "lancedb" not in dependencies
    assert "lance-namespace" not in dependencies
    assert "lance-namespace-urllib3-client" not in dependencies


def test_optional_dependencies_do_not_ship_lancedb_backend() -> None:
    """Keep optional extras from reintroducing the retired LanceDB backend."""
    optional_dependencies = _project()["project"]["optional-dependencies"]
    dependency_names = {
        dependency.split("==", maxsplit=1)[0].lower()
        for dependencies in optional_dependencies.values()
        for dependency in dependencies
    }

    assert "hosted-legacy-index" not in optional_dependencies
    assert "lancedb" not in dependency_names
    assert "lance-namespace" not in dependency_names
    assert "lance-namespace-urllib3-client" not in dependency_names


def test_package_metadata_is_ready_for_public_install_channels() -> None:
    """Keep PyPI and GitHub release metadata useful for first-time installers."""
    project = _project()["project"]

    assert project["license"] == {"file": "LICENSE"}
    assert project["authors"] == [{"name": "Nnenna Ndukwe"}]
    assert project["keywords"] == [
        "ai-agents",
        "cli",
        "code-quality",
        "mcp",
        "nvidia-nim",
        "policy",
        "preflight",
        "verification",
    ]
    assert project["urls"]["Repository"] == "https://github.com/nnennandukwe/policyNIM"
    assert project["urls"]["Issues"] == "https://github.com/nnennandukwe/policyNIM/issues"
    assert project["urls"]["Security"] == (
        "https://github.com/nnennandukwe/policyNIM/blob/main/SECURITY.md"
    )
    assert (
        project["urls"]["Support"]
        == "https://github.com/nnennandukwe/policyNIM/blob/main/SUPPORT.md"
    )
    assert project["urls"]["Roadmap"] == (
        "https://github.com/nnennandukwe/policyNIM/blob/main/docs/roadmap.md"
    )
    assert project["urls"]["Releases"] == "https://github.com/nnennandukwe/policyNIM/releases"
    assert project["urls"]["Changelog"] == (
        "https://github.com/nnennandukwe/policyNIM/blob/main/CHANGELOG.md"
    )
    assert "Programming Language :: Python :: 3.11" in project["classifiers"]


def test_package_advertises_inline_type_information() -> None:
    """Let downstream integrators know the installed package includes type hints."""
    project = _project()["project"]

    assert "Typing :: Typed" in project["classifiers"]
    assert (REPO_ROOT / "src" / "policynim" / "py.typed").read_text(encoding="utf-8").strip() == ""


def test_wheel_force_include_avoids_duplicate_package_resources() -> None:
    """Assets and templates already live under the package and must not be duplicated."""
    force_include = _project()["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert force_include == {
        "policies": "policynim/policies",
        "evals": "policynim/evals",
    }
