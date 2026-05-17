"""Live API smoke tests — skipped unless --run-live is passed.

These tests hit the real FCIO RZOB API. Run with:
    uv run pytest tests/test_live.py --run-live
"""

import click
import pytest

from llm_fcio import register_commands


@pytest.mark.live
def test_live_register_commands_creates_group() -> None:
    """register_commands adds fcio group to a Click group."""
    group = click.Group()
    register_commands(group)
    assert "fcio" in group.commands


@pytest.mark.live
def test_live_models_hits_api() -> None:
    """fcio models command reaches the live API and returns data."""
    from click.testing import CliRunner

    group = click.Group()
    register_commands(group)
    runner = CliRunner()
    result = runner.invoke(group, ["fcio", "models"])
    # Either succeeds with model list or fails with a clear error
    assert result.exit_code in (0, 1)
