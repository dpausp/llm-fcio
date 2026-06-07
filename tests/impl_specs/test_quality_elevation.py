"""Contract tests for quality-elevation spec (xfail).

Defines what each decision in .agents/impl_specs/quality-elevation.md
must fulfill. Tests are marked xfail — XPASS means the contract is met,
XFAIL means the contract is pending implementation.

Decisions covered:
  custom-exceptions, ruff-strict-config, type-annotations,
  test-infrastructure, nesting-reduction, complexity-gate,
  dead-code-forwarding, subprocess-hardening, httpx-status-codes
"""

import ast
import inspect
import textwrap
from pathlib import Path

import click
import httpx
import pytest

# ═══════════════════════════════════════════════════════════════════
# custom-exceptions
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.xfail(reason="custom-exceptions: ModelError class exists")
def test_model_error_exists() -> None:
    from llm_fcio import ModelError

    assert issubclass(ModelError, Exception)


@pytest.mark.xfail(reason="custom-exceptions: ModelError does not inherit ClickException")
def test_model_error_not_click_exception() -> None:
    from llm_fcio import ModelError

    assert not issubclass(ModelError, click.ClickException)


@pytest.mark.xfail(reason="custom-exceptions: ApiError class exists")
def test_api_error_exists() -> None:
    from llm_fcio import ApiError

    assert issubclass(ApiError, Exception)


@pytest.mark.xfail(reason="custom-exceptions: ApiError does not inherit ClickException")
def test_api_error_not_click_exception() -> None:
    from llm_fcio import ApiError

    assert not issubclass(ApiError, click.ClickException)


@pytest.mark.xfail(reason="custom-exceptions: ApiError has status_code attribute")
def test_api_error_status_code_attribute() -> None:
    from llm_fcio import ApiError

    err = ApiError("test", status_code=400)
    assert err.status_code == 400


@pytest.mark.xfail(reason="custom-exceptions: ApiError status_code defaults to None")
def test_api_error_status_code_defaults_none() -> None:
    from llm_fcio import ApiError

    err = ApiError("test")
    assert err.status_code is None


@pytest.mark.xfail(reason="custom-exceptions: ApiError message is accessible")
def test_api_error_message_accessible() -> None:
    from llm_fcio import ApiError

    err = ApiError("something broke", status_code=500)
    assert str(err) == "something broke"


@pytest.mark.xfail(reason="custom-exceptions: _resolve_model raises ModelError on ambiguous match")
def test_resolve_model_raises_model_error_ambiguous() -> None:
    from unittest.mock import patch

    from llm_fcio import ModelError, _resolve_model

    with patch("llm_fcio.api_request") as mock_api:
        mock_api.return_value.json.return_value = {
            "data": [
                {"id": "gpt-oss:20b"},
                {"id": "gpt-oss:20b-v2"},
            ]
        }
        with (
            patch("llm_fcio.shutil.which", return_value=None),
            pytest.raises(ModelError, match="Ambiguous"),
        ):
            # Use partial hint "gpt-oss" that matches both models
            _resolve_model("gpt-oss", "key", "https://api.example.com")


@pytest.mark.xfail(reason="custom-exceptions: _resolve_model raises ModelError on unknown model")
def test_resolve_model_raises_model_error_unknown() -> None:
    from unittest.mock import patch

    from llm_fcio import ModelError, _resolve_model

    with patch("llm_fcio.api_request") as mock_api:
        mock_api.return_value.json.return_value = {"data": [{"id": "gpt-oss:20b"}]}
        with (
            patch("llm_fcio.shutil.which", return_value=None),
            pytest.raises(ModelError, match="Unknown model"),
        ):
            _resolve_model("nonexistent", "key", "https://api.example.com")


@pytest.mark.xfail(reason="custom-exceptions: _extract_content raises ApiError on empty choices")
def test_extract_content_raises_api_error() -> None:
    from llm_fcio import ApiError, _extract_content

    with pytest.raises(ApiError, match="Empty response"):
        _extract_content({})


@pytest.mark.xfail(reason="custom-exceptions: api_request raises ApiError on bad status")
def test_api_request_raises_api_error_on_bad_status() -> None:
    from unittest.mock import patch

    from llm_fcio import ApiError, api_request

    mock_response = httpx.Response(500, json={"error": {"message": "internal"}})
    with patch("llm_fcio._make_client") as mock_client_ctx:
        mock_client = mock_client_ctx.return_value.__enter__.return_value
        mock_client.request.return_value = mock_response
        with pytest.raises(ApiError) as exc_info:
            api_request("GET", "/models", "key", "https://api.example.com")
        assert exc_info.value.status_code == 500


# ═══════════════════════════════════════════════════════════════════
# ruff-strict-config
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.xfail(reason="ruff-strict-config: [tool.ruff] section exists")
def test_ruff_config_section_exists(pyproject_toml: dict) -> None:
    assert "ruff" in pyproject_toml.get("tool", {})


@pytest.mark.xfail(reason="ruff-strict-config: target-version is py314")
def test_ruff_target_version(pyproject_toml: dict) -> None:
    assert pyproject_toml["tool"]["ruff"]["target-version"] == "py314"


@pytest.mark.xfail(reason="ruff-strict-config: line-length is 100")
def test_ruff_line_length(pyproject_toml: dict) -> None:
    assert pyproject_toml["tool"]["ruff"]["line-length"] == 100


@pytest.mark.xfail(reason="ruff-strict-config: [tool.ruff.lint] section exists")
def test_ruff_lint_section_exists(pyproject_toml: dict) -> None:
    assert "lint" in pyproject_toml["tool"]["ruff"]


@pytest.mark.xfail(reason="ruff-strict-config: core rule categories selected")
def test_ruff_lint_rule_categories(pyproject_toml: dict) -> None:
    selected = set(pyproject_toml["tool"]["ruff"]["lint"]["select"])
    required = {"E", "F", "I", "N", "W", "UP", "B", "C4", "SIM", "TCH", "BLE", "B904"}
    assert required <= selected, f"Missing rule categories: {required - selected}"


@pytest.mark.xfail(reason="ruff-strict-config: E501 is ignored")
def test_ruff_e501_ignored(pyproject_toml: dict) -> None:
    assert "E501" in pyproject_toml["tool"]["ruff"]["lint"]["ignore"]


@pytest.mark.xfail(reason="ruff-strict-config: [tool.ruff.format] section exists")
def test_ruff_format_section_exists(pyproject_toml: dict) -> None:
    assert "format" in pyproject_toml["tool"]["ruff"]


@pytest.mark.xfail(reason="ruff-strict-config: quote-style is double")
def test_ruff_format_quote_style(pyproject_toml: dict) -> None:
    assert pyproject_toml["tool"]["ruff"]["format"]["quote-style"] == "double"


# ═══════════════════════════════════════════════════════════════════
# type-annotations
# ═══════════════════════════════════════════════════════════════════


def _get_function(name: str) -> object:
    """Get a callable from llm_fcio by name."""
    from collections.abc import Callable

    import llm_fcio

    fn = getattr(llm_fcio, name)
    assert isinstance(fn, Callable)
    return fn


@pytest.mark.xfail(reason="type-annotations: _build_messages has return type")
def test_build_messages_return_type() -> None:
    sig = inspect.signature(_get_function("_build_messages"))  # type: ignore[arg-type]
    assert sig.return_annotation != inspect.Parameter.empty


@pytest.mark.xfail(reason="type-annotations: _build_messages has parameter types")
def test_build_messages_parameter_types() -> None:
    sig = inspect.signature(_get_function("_build_messages"))  # type: ignore[arg-type]
    for param in sig.parameters.values():
        assert param.annotation != inspect.Parameter.empty, (
            f"Parameter {param.name} missing type annotation"
        )


@pytest.mark.xfail(reason="type-annotations: _iter_sse_content has return type")
def test_iter_sse_content_return_type() -> None:
    sig = inspect.signature(_get_function("_iter_sse_content"))  # type: ignore[arg-type]
    assert sig.return_annotation != inspect.Parameter.empty


@pytest.mark.xfail(reason="type-annotations: _extract_content has return type")
def test_extract_content_return_type() -> None:
    sig = inspect.signature(_get_function("_extract_content"))  # type: ignore[arg-type]
    assert sig.return_annotation != inspect.Parameter.empty


@pytest.mark.xfail(reason="type-annotations: api_request has return type")
def test_api_request_return_type() -> None:
    sig = inspect.signature(_get_function("api_request"))  # type: ignore[arg-type]
    assert sig.return_annotation != inspect.Parameter.empty


@pytest.mark.xfail(reason="type-annotations: api_request has parameter types")
def test_api_request_parameter_types() -> None:
    sig = inspect.signature(_get_function("api_request"))  # type: ignore[arg-type]
    for param in sig.parameters.values():
        assert param.annotation != inspect.Parameter.empty, (
            f"Parameter {param.name} missing type annotation"
        )


@pytest.mark.xfail(reason="type-annotations: _chunk_lines has return type")
def test_chunk_lines_return_type() -> None:
    sig = inspect.signature(_get_function("_chunk_lines"))  # type: ignore[arg-type]
    assert sig.return_annotation != inspect.Parameter.empty


@pytest.mark.xfail(reason="type-annotations: _discover_files has return type")
def test_discover_files_return_type() -> None:
    sig = inspect.signature(_get_function("_discover_files"))  # type: ignore[arg-type]
    assert sig.return_annotation != inspect.Parameter.empty


@pytest.mark.xfail(reason="type-annotations: _make_client has return type")
def test_make_client_return_type() -> None:
    sig = inspect.signature(_get_function("_make_client"))  # type: ignore[arg-type]
    assert sig.return_annotation != inspect.Parameter.empty


@pytest.mark.xfail(reason="type-annotations: get_api_key has return type")
def test_get_api_key_return_type() -> None:
    sig = inspect.signature(_get_function("get_api_key"))  # type: ignore[arg-type]
    assert sig.return_annotation != inspect.Parameter.empty


@pytest.mark.xfail(reason="type-annotations: register_models has return type")
def test_register_models_return_type() -> None:
    sig = inspect.signature(_get_function("register_models"))  # type: ignore[arg-type]
    assert sig.return_annotation != inspect.Parameter.empty


@pytest.mark.xfail(reason="type-annotations: collect_code_files has return type")
def test_collect_code_files_return_type() -> None:
    sig = inspect.signature(_get_function("collect_code_files"))  # type: ignore[arg-type]
    assert sig.return_annotation != inspect.Parameter.empty


# ═══════════════════════════════════════════════════════════════════
# test-infrastructure
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.xfail(reason="test-infrastructure: tests/ directory exists")
def test_tests_directory_exists(project_root: Path) -> None:
    assert (project_root / "tests").is_dir()


@pytest.mark.xfail(reason="test-infrastructure: tests/conftest.py exists")
def test_conftest_exists(project_root: Path) -> None:
    assert (project_root / "tests" / "conftest.py").is_file()


@pytest.mark.xfail(reason="test-infrastructure: pytest in dev dependencies")
def test_pytest_in_dev_deps(pyproject_toml: dict) -> None:
    dev_deps = pyproject_toml.get("dependency-groups", {}).get("dev", [])
    dep_names = [d if isinstance(d, str) else d.get("package", "") for d in dev_deps]
    assert any("pytest" in str(d) for d in dep_names), "pytest not in dev deps"


@pytest.mark.xfail(reason="test-infrastructure: pytest-cov in dev dependencies")
def test_pytest_cov_in_dev_deps(pyproject_toml: dict) -> None:
    dev_deps = pyproject_toml.get("dependency-groups", {}).get("dev", [])
    dep_names = [d if isinstance(d, str) else d.get("package", "") for d in dev_deps]
    assert any("pytest-cov" in str(d) for d in dep_names), "pytest-cov not in dev deps"


@pytest.mark.xfail(reason="test-infrastructure: [tool.pytest.ini_options] configured")
def test_pytest_ini_options(pyproject_toml: dict) -> None:
    assert "pytest" in pyproject_toml.get("tool", {})


@pytest.mark.xfail(reason="test-infrastructure: testpaths configured")
def test_pytest_testpaths(pyproject_toml: dict) -> None:
    pytest_opts = pyproject_toml["tool"]["pytest"]["ini_options"]
    assert "tests" in pytest_opts.get("testpaths", [])


# ═══════════════════════════════════════════════════════════════════
# nesting-reduction
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.xfail(reason="nesting-reduction: _iter_sse_content function exists")
def test_iter_sse_content_exists() -> None:
    import llm_fcio

    assert callable(getattr(llm_fcio, "_iter_sse_content", None))


@pytest.mark.xfail(
    reason="nesting-reduction: _iter_sse_content returns tuple with metadata and iterator"
)
def test_iter_sse_content_return_structure() -> None:
    import llm_fcio

    sig = inspect.signature(llm_fcio._iter_sse_content)
    ret = sig.return_annotation
    # Return should be a tuple type — check stringified annotation contains 'tuple'
    assert "tuple" in str(ret).lower(), f"Expected tuple return, got {ret}"


@pytest.mark.xfail(reason="nesting-reduction: _SSEMetadata dataclass exists")
def test_sse_metadata_exists() -> None:
    import llm_fcio

    assert hasattr(llm_fcio, "_SSEMetadata")


@pytest.mark.xfail(reason="nesting-reduction: execute uses _iter_sse_content, not inline SSE loop")
def test_execute_uses_iter_sse_content() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio.RzobModel.execute)
    assert "_iter_sse_content" in source, "execute() must call _iter_sse_content"


@pytest.mark.xfail(reason="nesting-reduction: _stream_chat_response uses _iter_sse_content")
def test_stream_chat_response_uses_iter_sse_content() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio._stream_chat_response)
    assert "_iter_sse_content" in source, "_stream_chat_response must call _iter_sse_content"


@pytest.mark.xfail(reason="nesting-reduction: no duplicated connect_sse in execute")
def test_execute_no_inline_connect_sse() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio.RzobModel.execute)
    assert "connect_sse" not in source, "execute() must not contain inline connect_sse"


# ═══════════════════════════════════════════════════════════════════
# complexity-gate
# ═══════════════════════════════════════════════════════════════════


def _compute_cc(source: str) -> int:
    """Compute McCabe cyclomatic complexity of a function source string."""
    tree = ast.parse(textwrap.dedent(source))
    # Count decision points: if, for, while, and, or, except, with, assert, comprehension
    complexity = 1
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.ExceptHandler)):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            complexity += sum(1 for _ in node.generators)
        elif isinstance(node, ast.Assert):
            complexity += 1
    return complexity


def _function_cc(obj: object, method_name: str | None = None) -> int:
    """Get cyclomatic complexity of a function or method."""
    from collections.abc import Callable

    target = getattr(obj, method_name) if method_name else obj
    assert isinstance(target, Callable)
    source = inspect.getsource(target)
    return _compute_cc(source)


@pytest.mark.xfail(reason="complexity-gate: _iter_sse_content CC <= 15")
def test_iter_sse_content_complexity() -> None:
    import llm_fcio

    cc = _function_cc(llm_fcio._iter_sse_content)
    assert cc <= 15, f"_iter_sse_content CC={cc}, exceeds threshold of 15"


@pytest.mark.xfail(reason="complexity-gate: cmd_capabilities CC <= 15")
def test_cmd_capabilities_complexity() -> None:
    import llm_fcio

    cc = _function_cc(llm_fcio.cmd_capabilities)
    assert cc <= 15, f"cmd_capabilities CC={cc}, exceeds threshold of 15"


@pytest.mark.xfail(reason="complexity-gate: _make_client CC <= 15")
def test_make_client_complexity() -> None:
    import llm_fcio

    cc = _function_cc(llm_fcio._make_client)
    assert cc <= 15, f"_make_client CC={cc}, exceeds threshold of 15"


@pytest.mark.xfail(reason="complexity-gate: _build_messages CC <= 15")
def test_build_messages_complexity() -> None:
    import llm_fcio

    cc = _function_cc(llm_fcio._build_messages)
    assert cc <= 15, f"_build_messages CC={cc}, exceeds threshold of 15"


@pytest.mark.xfail(reason="complexity-gate: _stream_chat_response CC <= 15")
def test_stream_chat_response_complexity() -> None:
    import llm_fcio

    cc = _function_cc(llm_fcio._stream_chat_response)
    assert cc <= 15, f"_stream_chat_response CC={cc}, exceeds threshold of 15"


# ═══════════════════════════════════════════════════════════════════
# dead-code-forwarding
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.xfail(reason="dead-code-forwarding: execute forwards tools option")
def test_execute_forwards_tools() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio.RzobModel.execute)
    assert "tools" in source, "execute() must forward tools option"
    # Must reference prompt.options.tools and body["tools"]
    assert "prompt.options.tools" in source or "options.tools" in source
    assert 'body["tools"]' in source or "body['tools']" in source


@pytest.mark.xfail(reason="dead-code-forwarding: execute forwards response_format option")
def test_execute_forwards_response_format() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio.RzobModel.execute)
    assert "response_format" in source, "execute() must forward response_format option"
    # Must reference prompt.options.response_format and body["response_format"]
    assert "prompt.options.response_format" in source or "options.response_format" in source
    assert 'body["response_format"]' in source or "body['response_format']" in source


@pytest.mark.xfail(reason="dead-code-forwarding: tools forwarding is conditional (not always set)")
def test_execute_tools_forwarding_is_conditional() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio.RzobModel.execute)
    # Must have an 'if ... tools is not None' guard before the body assignment
    assert "if" in source and "tools" in source and "None" in source, (
        "tools forwarding must be conditional"
    )
    # The 'if' must appear BEFORE the body["tools"] assignment
    if_pos = source.find("if")
    body_tools_pos = source.find('body["tools"]')
    if body_tools_pos == -1:
        body_tools_pos = source.find("body['tools']")
    assert if_pos < body_tools_pos, "if-check must precede body assignment for tools"


@pytest.mark.xfail(reason="dead-code-forwarding: response_format forwarding is conditional")
def test_execute_response_format_forwarding_is_conditional() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio.RzobModel.execute)
    # At least the options-based forwarding must be conditional
    # (the schema block sets response_format unconditionally, which is fine)
    assert "if" in source and "response_format" in source, (
        "response_format forwarding must have conditional check"
    )
    # Find the options-based forwarding: if prompt.options.response_format
    assert "prompt.options.response_format" in source or "options.response_format" in source


# ═══════════════════════════════════════════════════════════════════
# subprocess-hardening
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.xfail(reason="subprocess-hardening: shutil module is imported")
def test_shutil_imported() -> None:
    import llm_fcio

    assert hasattr(llm_fcio, "shutil") or "shutil" in dir(llm_fcio)


@pytest.mark.xfail(reason="subprocess-hardening: shutil.which used before subprocess call")
def test_shutil_which_check_present() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio._resolve_model)
    assert 'shutil.which("fzf")' in source or "shutil.which(" in source


@pytest.mark.xfail(reason="subprocess-hardening: check=False on subprocess.run")
def test_subprocess_check_false() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio._resolve_model)
    assert "check=False" in source, "subprocess.run must use check=False"


@pytest.mark.xfail(reason="subprocess-hardening: timeout=10 on subprocess.run")
def test_subprocess_timeout() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio._resolve_model)
    assert "timeout=10" in source, "subprocess.run must have timeout=10"


@pytest.mark.xfail(reason="subprocess-hardening: fzf_path variable from shutil.which")
def test_subprocess_uses_which_result() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio._resolve_model)
    # Should store shutil.which result and use it as the binary path
    assert "fzf_path" in source, "must store shutil.which result in a variable"


# ═══════════════════════════════════════════════════════════════════
# httpx-status-codes
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.xfail(reason="httpx-status-codes: uses httpx.codes.BAD_REQUEST for >= 400 check")
def test_httpx_bad_request_constant() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio.api_request)
    assert "httpx.codes.BAD_REQUEST" in source, "Must use httpx.codes.BAD_REQUEST"


@pytest.mark.xfail(reason="httpx-status-codes: uses httpx.codes.UNAUTHORIZED constant")
def test_httpx_unauthorized_constant() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio.get_capabilities)
    assert "httpx.codes.UNAUTHORIZED" in source, "Must use httpx.codes.UNAUTHORIZED"


@pytest.mark.xfail(reason="httpx-status-codes: uses httpx.codes.NOT_FOUND constant")
def test_httpx_not_found_constant() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio.get_model_info)
    assert "httpx.codes.NOT_FOUND" in source, "Must use httpx.codes.NOT_FOUND"


@pytest.mark.xfail(
    reason="httpx-status-codes: no raw integer status code comparisons in api_request"
)
def test_no_raw_status_in_api_request() -> None:
    import llm_fcio

    source = inspect.getsource(llm_fcio.api_request)
    lines = source.splitlines()
    for line in lines:
        # Should not have bare >= 400 or == 401 etc
        if "status_code" in line:
            assert "= 400" not in line or "BAD_REQUEST" in line, (
                f"Raw status code found: {line.strip()}"
            )
            assert "= 401" not in line or "UNAUTHORIZED" in line, (
                f"Raw status code found: {line.strip()}"
            )
