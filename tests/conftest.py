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


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "live: requires --run-live (real API call)")
    config.addinivalue_line("markers", "e2e: end-to-end tests against real HTTP servers (skvaider or dummy)")


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add --run-live CLI option to enable live API tests."""
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked @pytest.mark.live (requires real API access)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live tests unless --run-live was passed."""
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --run-live option")
    for item in items:
        if "live" in [m.name for m in item.iter_markers()]:
            item.add_marker(skip_live)
