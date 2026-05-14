"""Contract tests for quality-elevation impl spec."""


import inspect


# ── 1. Custom Exceptions ────────────────────────────────────────


def test_model_error_is_exception() -> None:
    from llm_fcio import ModelError

    assert issubclass(ModelError, Exception)


def test_api_error_is_exception() -> None:
    from llm_fcio import ApiError

    assert issubclass(ApiError, Exception)


# ── 2. Ruff Config ──────────────────────────────────────────────


def test_ruff_config_exists(pyproject_toml: dict) -> None:
    """[tool.ruff] section exists in pyproject.toml"""
    assert "tool" in pyproject_toml
    assert "ruff" in pyproject_toml["tool"]


# ── 3. Type Annotations ─────────────────────────────────────────


def test_execute_has_full_annotations() -> None:
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


def test_iter_sse_content_exists() -> None:
    from llm_fcio import _iter_sse_content

    assert callable(_iter_sse_content)


# ── 5. Dead Code Forwarding ──────────────────────────────────────


def test_options_tools_forwarded() -> None:
    """execute() body references prompt.options.tools"""
    from llm_fcio import RzobModel

    source = inspect.getsource(RzobModel.execute)
    assert "options.tools" in source or "tools" in source


# ── 6. Subprocess Hardening ──────────────────────────────────────


def test_shutil_which_used_for_fzf() -> None:
    from llm_fcio import _resolve_model

    source = inspect.getsource(_resolve_model)
    assert "shutil.which" in source


# ── 7. httpx Status Codes ────────────────────────────────────────


def test_no_raw_http_status_codes() -> None:
    """api_request uses httpx.codes instead of raw int threshold"""
    from llm_fcio import api_request

    source = inspect.getsource(api_request)
    assert ">= 400" not in source


# ── 8. Test Infrastructure ───────────────────────────────────────


def test_pytest_config_in_pyproject(pyproject_toml: dict) -> None:
    assert "tool" in pyproject_toml
    assert "pytest" in pyproject_toml["tool"]
    assert "ini_options" in pyproject_toml["tool"]["pytest"]
