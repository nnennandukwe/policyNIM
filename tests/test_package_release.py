"""Package metadata checks for direct CLI installs."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _project() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_runtime_dependencies_pin_lance_namespace_for_clean_wheel_installs() -> None:
    """Prevent pip/pipx from resolving a lancedb-incompatible namespace client."""
    dependencies = set(_project()["project"]["dependencies"])

    assert "lance-namespace==0.6.1" in dependencies
    assert "lance-namespace-urllib3-client==0.6.1" in dependencies


def test_package_metadata_is_ready_for_public_install_channels() -> None:
    """Keep PyPI and GitHub release metadata useful for first-time installers."""
    project = _project()["project"]

    assert project["license"] == {"file": "LICENSE"}
    assert project["authors"] == [{"name": "Nnenna Ndukwe"}]
    assert project["keywords"] == ["ai-agents", "mcp", "nvidia-nim", "policy", "preflight"]
    assert project["urls"]["Repository"] == "https://github.com/nnennandukwe/policyNIM"
    assert project["urls"]["Issues"] == "https://github.com/nnennandukwe/policyNIM/issues"
    assert "Programming Language :: Python :: 3.11" in project["classifiers"]


def test_wheel_force_include_avoids_duplicate_package_resources() -> None:
    """Assets and templates already live under the package and must not be duplicated."""
    force_include = _project()["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert force_include == {
        "policies": "policynim/policies",
        "evals": "policynim/evals",
    }
