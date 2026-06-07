"""Tests for pytest failure analyzer functions.

Covers collect_failures and build_failure_prompt — pure functions
that extract failure info from pytest reports and build LLM prompts.
"""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from tests.fakes import FakeModel, FakeResponse

try:
    from llm_fcio import build_failure_prompt, collect_failures
except ImportError:
    collect_failures = None  # type: ignore[assignment,misc]
    build_failure_prompt = None  # type: ignore[assignment,misc]

try:
    from llm_fcio import analyze_failures
except ImportError:
    analyze_failures = None  # type: ignore[assignment,misc]


# ── Fakes for pytest.TestReport ───────────────────────────────────


@dataclass
class FakeExcInfo:
    """Minimal stand-in for pytest ExceptionInfo."""

    _message: str = "AssertionError: assert False"

    def exconly(self, tryshort: bool = False) -> str:
        return self._message


@dataclass
class FakeCall:
    """Minimal stand-in for pytest CallInfo."""

    excinfo: FakeExcInfo | None = None


@dataclass
class FakeTestReport:
    """Minimal stand-in for pytest.TestReport with relevant fields."""

    nodeid: str = "test_example.py::test_fail"
    outcome: str = "failed"
    when: str = "call"
    longrepr: str = "def test_fail():\n    assert False"
    call: FakeCall | None = None


def _make_failed_report(
    nodeid: str = "test_example.py::test_fail",
    message: str = "AssertionError: assert False",
    traceback: str = "def test_fail():\n    assert False",
    when: str = "call",
) -> FakeTestReport:
    """Create a FakeTestReport simulating a call-phase failure."""
    return FakeTestReport(
        nodeid=nodeid,
        outcome="failed",
        when=when,
        longrepr=traceback,
        call=FakeCall(excinfo=FakeExcInfo(_message=message)),
    )


# ── collect_failures ──────────────────────────────────────────────


def test_collect_failures_single_report() -> None:
    report = _make_failed_report(
        nodeid="test_math.py::test_add",
        message="AssertionError: assert 1 == 2",
        traceback="def test_add():\n    assert 1 == 2",
    )
    result = collect_failures([report])
    assert len(result) == 1
    assert result[0]["test_name"] == "test_math.py::test_add"
    assert result[0]["outcome"] == "failed"
    assert result[0]["message"] == "AssertionError: assert 1 == 2"
    assert result[0]["traceback"] == "def test_add():\n    assert 1 == 2"


def test_collect_failures_multiple_failures() -> None:
    reports = [
        _make_failed_report(nodeid="test_a.py::test_one", message="assert False"),
        _make_failed_report(nodeid="test_b.py::test_two", message="TypeError: bad call"),
    ]
    result = collect_failures(reports)
    assert len(result) == 2
    assert result[0]["test_name"] == "test_a.py::test_one"
    assert result[1]["test_name"] == "test_b.py::test_two"


def test_collect_failures_skips_passed_reports() -> None:
    failed = _make_failed_report(nodeid="test_a.py::test_fail")
    passed = FakeTestReport(nodeid="test_a.py::test_pass", outcome="passed")
    result = collect_failures([failed, passed])
    assert len(result) == 1
    assert result[0]["test_name"] == "test_a.py::test_fail"


def test_collect_failures_skips_skipped_reports() -> None:
    failed = _make_failed_report(nodeid="test_a.py::test_fail")
    skipped = FakeTestReport(nodeid="test_a.py::test_skip", outcome="skipped")
    result = collect_failures([failed, skipped])
    assert len(result) == 1
    assert result[0]["test_name"] == "test_a.py::test_fail"


def test_collect_failures_setup_failure_without_call() -> None:
    """Setup/teardown failures may lack call info — handle gracefully."""
    report = FakeTestReport(
        nodeid="test_fix.py::test_with_fixture",
        outcome="failed",
        when="setup",
        longrepr="fixture 'db' not found",
        call=None,
    )
    result = collect_failures([report])
    assert len(result) == 1
    assert result[0]["test_name"] == "test_fix.py::test_with_fixture"
    assert result[0]["outcome"] == "failed"
    assert result[0]["message"] != ""
    assert result[0]["traceback"] != ""


def test_collect_failures_empty_list() -> None:
    result = collect_failures([])
    assert result == []


def test_collect_failures_output_has_required_keys() -> None:
    report = _make_failed_report()
    result = collect_failures([report])
    assert len(result) == 1
    expected_keys = {"test_name", "outcome", "message", "traceback"}
    assert set(result[0].keys()) == expected_keys


# ── build_failure_prompt ──────────────────────────────────────────


def _sample_failure(
    test_name: str = "test_sample.py::test_example",
    message: str = "AssertionError: assert False",
) -> dict[str, str]:
    """Create a minimal failure dict for prompt tests."""
    return {
        "test_name": test_name,
        "outcome": "failed",
        "message": message,
        "traceback": f"def {test_name.split('::')[1]}():\n    assert False",
    }


def test_build_failure_prompt_returns_string() -> None:
    failures = [_sample_failure()]
    prompt = build_failure_prompt(failures)
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_build_failure_prompt_includes_test_name() -> None:
    failures = [_sample_failure(test_name="test_auth.py::test_login")]
    prompt = build_failure_prompt(failures)
    assert "test_auth.py::test_login" in prompt


def test_build_failure_prompt_includes_failure_message() -> None:
    failures = [_sample_failure(message="ValueError: invalid token")]
    prompt = build_failure_prompt(failures)
    assert "ValueError: invalid token" in prompt


def test_build_failure_prompt_default_focus_is_quick() -> None:
    failures = [_sample_failure()]
    prompt_default = build_failure_prompt(failures)
    prompt_explicit = build_failure_prompt(failures, focus="quick")
    assert prompt_default == prompt_explicit


def test_build_failure_prompt_fix_focus_differs_from_quick() -> None:
    failures = [_sample_failure()]
    prompt_quick = build_failure_prompt(failures, focus="quick")
    prompt_fix = build_failure_prompt(failures, focus="fix")
    assert prompt_quick != prompt_fix


def test_build_failure_prompt_root_cause_focus_differs() -> None:
    failures = [_sample_failure()]
    prompt_quick = build_failure_prompt(failures, focus="quick")
    prompt_root = build_failure_prompt(failures, focus="root-cause")
    assert prompt_quick != prompt_root


def test_build_failure_prompt_multiple_failures_included() -> None:
    failures = [
        _sample_failure(test_name="test_a.py::test_one"),
        _sample_failure(test_name="test_b.py::test_two"),
    ]
    prompt = build_failure_prompt(failures)
    assert "test_a.py::test_one" in prompt
    assert "test_b.py::test_two" in prompt


def test_build_failure_prompt_empty_failures() -> None:
    prompt = build_failure_prompt([])
    assert isinstance(prompt, str)


# ── analyze_failures ──────────────────────────────────────────────


def _analysis_failure(
    test_name: str = "test_sample.py::test_example",
    message: str = "AssertionError: assert False",
) -> dict[str, str]:
    """Create a minimal failure dict for analyze tests."""
    return {
        "test_name": test_name,
        "outcome": "failed",
        "message": message,
        "traceback": f"def {test_name.split('::')[1]}():\n    assert False",
    }


def test_analyze_failures_empty_failures_no_api_call() -> None:
    """Empty failures list returns '' without calling llm.get_model."""
    with patch("llm_fcio.llm.get_model") as mock_get_model:
        result = analyze_failures([])
    assert result == ""
    mock_get_model.assert_not_called()


def test_analyze_failures_calls_model_with_prompt() -> None:
    """analyze_failures sends the built prompt to the model."""
    fake_response = FakeResponse(chunks=("Analysis result",))
    fake_model = FakeModel(response=fake_response)
    with patch("llm_fcio.llm.get_model", return_value=fake_model):
        analyze_failures([_analysis_failure()])
    # model.prompt() was called — check the prompt contains the test name
    prompt_text = fake_model.last_prompt_args[0] if fake_model.last_prompt_args else ""
    assert "test_sample.py::test_example" in prompt_text


def test_analyze_failures_returns_model_response() -> None:
    """analyze_failures returns the model's response text."""
    fake_response = FakeResponse(chunks=("Analysis: fix X",))
    fake_model = FakeModel(response=fake_response)
    with patch("llm_fcio.llm.get_model", return_value=fake_model):
        result = analyze_failures([_analysis_failure()])
    assert result == "Analysis: fix X"


def test_analyze_failures_passes_focus_to_prompt() -> None:
    """focus='fix' produces a prompt containing the fix instruction."""
    fake_response = FakeResponse()
    fake_model = FakeModel(response=fake_response)
    with patch("llm_fcio.llm.get_model", return_value=fake_model):
        analyze_failures([_analysis_failure()], focus="fix")
    prompt_text = fake_model.last_prompt_args[0] if fake_model.last_prompt_args else ""
    assert "code suggestions" in prompt_text


# ── analyze_code ──────────────────────────────────────────────────


def test_analyze_code_with_files_returns_text(tmp_path: Path) -> None:
    """analyze_code reads files and returns model response."""
    from llm_fcio import analyze_code

    code_file = tmp_path / "example.py"
    code_file.write_text("x = 1\n")

    fake_response = FakeResponse(chunks=("Code looks good",))
    fake_model = FakeModel(response=fake_response)
    with patch("llm_fcio.llm.get_model", return_value=fake_model):
        result = analyze_code("review", files=[str(code_file)], model_id="test-model")

    assert result == "Code looks good"
    assert fake_model.last_prompt_kwargs.get("system") is not None


def test_analyze_code_no_files_returns_empty() -> None:
    """analyze_code returns empty string when no files are found."""
    from llm_fcio import analyze_code

    with patch("llm_fcio.collect_code_files", return_value=[]):
        result = analyze_code("review", files=None)

    assert result == ""


def test_analyze_code_default_model_id(tmp_path: Path) -> None:
    """analyze_code uses fcio-{loc_name}/gpt-oss-20b-20b when model_id is None."""
    from llm_fcio import analyze_code

    code_file = tmp_path / "example.py"
    code_file.write_text("x = 1\n")

    fake_response = FakeResponse()
    fake_model = FakeModel(response=fake_response)
    with patch("llm_fcio.llm.get_model", return_value=fake_model) as mock_get:
        analyze_code("review", files=[str(code_file)], model_id=None, loc_name="rzob")

    mock_get.assert_called_once_with("fcio-rzob/gpt-oss-20b-20b")
