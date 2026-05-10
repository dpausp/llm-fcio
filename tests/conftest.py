"""Shared test fixtures for llm-fcio."""

from pathlib import Path

import pytest


@pytest.fixture
def project_root() -> Path:
    """Project root directory (where pyproject.toml lives)."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def pyproject_toml(project_root: Path) -> dict:
    """Parsed pyproject.toml as a dict."""
    import tomllib

    with open(project_root / "pyproject.toml", "rb") as f:
        return tomllib.load(f)
