"""Contract tests for quality-elevation impl spec.

All tests are marked xfail — they verify the *contract* (imports, config,
structure) that Phase 2 implementation must satisfy. Phase 2 makes them green.
"""

import inspect

import pytest


# ── 1. Custom Exceptions ────────────────────────────────────────


@pytest.mark.xfail(reason="impl spec contract — Phase 2 makes green")
def test_model_error_is_exception():
    from llm_fcio import ModelError

    assert issubclass(ModelError, Exception)


@pytest.mark.xfail(reason="impl spec contract — Phase 2 makes green")
def test_api_error_is_exception():
    from llm_fcio import ApiError

    assert issubclass(ApiError, Exception)


# ── 2. Ruff Config ──────────────────────────────────────────────


@pytest.mark.xfail(reason="impl spec contract — Phase 2 makes green")
def test_ruff_config_exists(pyproject_toml: dict):
    """[tool.ruff] section exists in pyproject.toml"""
    assert "tool" in pyproject_toml
    assert "ruff" in pyproject_toml["tool"]


# ── 3. Type Annotations ─────────────────────────────────────────


@pytest.mark.xfail(reason="impl spec contract — Phase 2 makes green")
def test_execute_has_full_annotations():
    from llm_fcio import RzobModel

    sig = inspect.signature(RzobModel.execute)
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        assert param.annotation is not inspect.Parameter.empty, (
            f"Parameter '{name}' missing type annotation"
        )
    assert sig.return_annotation is not inspect.Signature.empty


# ── 4. SSE Helper Extraction ─────────────────────────────────────


@pytest.mark.xfail(reason="impl spec contract — Phase 2 makes green")
def test_iter_sse_content_exists():
    from llm_fcio import _iter_sse_content

    assert callable(_iter_sse_content)


# ── 5. Dead Code Forwarding ──────────────────────────────────────


@pytest.mark.xfail(reason="impl spec contract — Phase 2 makes green")
def test_options_tools_forwarded():
    """execute() body references prompt.options.tools"""
    from llm_fcio import RzobModel

    source = inspect.getsource(RzobModel.execute)
    assert "options.tools" in source or "tools" in source


# ── 6. Subprocess Hardening ──────────────────────────────────────


@pytest.mark.xfail(reason="impl spec contract — Phase 2 makes green")
def test_shutil_which_used_for_fzf():
    from llm_fcio import _resolve_model

    source = inspect.getsource(_resolve_model)
    assert "shutil.which" in source


# ── 7. httpx Status Codes ────────────────────────────────────────


@pytest.mark.xfail(reason="impl spec contract — Phase 2 makes green")
def test_no_raw_http_status_codes():
    """api_request uses httpx.codes instead of raw int threshold"""
    from llm_fcio import api_request

    source = inspect.getsource(api_request)
    assert ">= 400" not in source


# ── 8. Test Infrastructure ───────────────────────────────────────


@pytest.mark.xfail(reason="impl spec contract — Phase 2 makes green")
def test_pytest_config_in_pyproject(pyproject_toml: dict):
    assert "tool" in pyproject_toml
    assert "pytest" in pyproject_toml["tool"]
    assert "ini_options" in pyproject_toml["tool"]["pytest"]
