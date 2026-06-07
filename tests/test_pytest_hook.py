"""Tests for pytest plugin hooks: pytest_addoption and pytest_terminal_summary.

Unit tests with mock objects — verifies the FCIO failure analyzer integrates
into pytest's terminal output via the plugin entry point.
"""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

# ── Fakes for pytest.TestReport ────────────────────────────────────


@dataclass
class _FakeExcInfo:
    """Minimal stand-in for pytest ExceptionInfo."""

    _message: str = "AssertionError: assert False"

    def exconly(self, *, tryshort: bool = False) -> str:
        return self._message


@dataclass
class _FakeCall:
    """Minimal stand-in for pytest CallInfo."""

    excinfo: _FakeExcInfo | None = None


@dataclass
class _FakeTestReport:
    """Minimal stand-in for pytest.TestReport with relevant fields."""

    nodeid: str = "test_example.py::test_fail"
    outcome: str = "failed"
    when: str = "call"
    longrepr: str = "def test_fail():\n    assert False"
    call: _FakeCall | None = None


def _make_failed_report(
    nodeid: str = "test_example.py::test_fail",
    message: str = "AssertionError: assert False",
    traceback: str = "def test_fail():\n    assert False",
) -> _FakeTestReport:
    """Create a FakeTestReport simulating a call-phase failure."""
    return _FakeTestReport(
        nodeid=nodeid,
        outcome="failed",
        when="call",
        longrepr=traceback,
        call=_FakeCall(excinfo=_FakeExcInfo(_message=message)),
    )


def _make_mock_config(
    *,
    analyze: bool = False,
    focus: str = "quick",
    model: str | None = None,
) -> MagicMock:
    """Create a mock config with getoption returning FCIO analyzer settings."""
    config = MagicMock()
    options = {
        "--fcio-analyze": analyze,
        "--fcio-focus": focus,
        "--fcio-model": model,
    }
    config.getoption.side_effect = lambda name: options[name]
    return config


# ── Import guards (hooks don't exist yet during xfail phase) ──────

try:
    from llm_fcio import pytest_addoption, pytest_terminal_summary
except ImportError:
    pytest_addoption = None  # type: ignore[assignment, misc]
    pytest_terminal_summary = None  # type: ignore[assignment, misc]


# ── Tests ──────────────────────────────────────────────────────────


def test_fcio_analyze_flag_registered() -> None:
    """--fcio-analyze is registered as a valid pytest CLI option."""
    mock_parser = MagicMock()
    mock_group = MagicMock()
    mock_parser.getgroup.return_value = mock_group

    pytest_addoption(mock_parser)

    mock_parser.getgroup.assert_called_once_with("fcio", "FCIO Failure Analyzer")

    option_names = [c.args[0] for c in mock_group.addoption.call_args_list]
    assert "--fcio-analyze" in option_names
    assert "--fcio-focus" in option_names
    assert "--fcio-model" in option_names


def test_fcio_analyze_not_active_by_default() -> None:
    """Without --fcio-analyze, no analysis output is produced."""
    mock_tr = MagicMock()
    mock_tr.stats = {"failed": [_make_failed_report()]}
    config = _make_mock_config(analyze=False)

    with patch("llm_fcio.analyze_failures") as mock_analyze:
        pytest_terminal_summary(mock_tr, exitstatus=1, config=config)

    mock_analyze.assert_not_called()
    mock_tr.write_line.assert_not_called()


def test_fcio_analyze_shows_analysis_on_failure() -> None:
    """With --fcio-analyze and failures, analysis is printed to terminal."""
    failed_report = _make_failed_report(
        nodeid="test_hook.py::test_bad",
        message="AssertionError: assert False",
    )

    mock_tr = MagicMock()
    mock_tr.stats = {"failed": [failed_report]}
    config = _make_mock_config(analyze=True)

    with patch("llm_fcio.analyze_failures", return_value="MOCK ANALYSIS OUTPUT"):
        pytest_terminal_summary(mock_tr, exitstatus=1, config=config)

    written = "\n".join(c.args[0] for c in mock_tr.write_line.call_args_list)
    assert "MOCK ANALYSIS OUTPUT" in written
    assert "FCIO Failure Analysis" in written


def test_fcio_analyze_no_failures_no_output() -> None:
    """With --fcio-analyze but no failures, no analysis section is printed."""
    mock_tr = MagicMock()
    mock_tr.stats = {}
    config = _make_mock_config(analyze=True)

    with patch("llm_fcio.analyze_failures") as mock_analyze:
        pytest_terminal_summary(mock_tr, exitstatus=0, config=config)

    mock_analyze.assert_not_called()
    mock_tr.write_line.assert_not_called()


def test_fcio_focus_option_passed() -> None:
    """The --fcio-focus value is passed through to analyze_failures."""
    failed_report = _make_failed_report()

    mock_tr = MagicMock()
    mock_tr.stats = {"failed": [failed_report]}
    config = _make_mock_config(analyze=True, focus="fix")

    with patch("llm_fcio.analyze_failures", return_value="FIX SUGGESTION") as mock_analyze:
        pytest_terminal_summary(mock_tr, exitstatus=1, config=config)

    mock_analyze.assert_called_once()
    assert mock_analyze.call_args.kwargs["focus"] == "fix"
