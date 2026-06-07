"""Contract tests for quality-elevation spec.

All tests are marked xfail — they define what a future implementation must fulfill.
Tests that XPASS indicate already-completed spec items.
Spec: .agents/impl_specs/quality-elevation.md
"""

import inspect
import re
import tomllib
from pathlib import Path

import click
import pytest

import llm_fcio

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _read_source() -> str:
    """Read llm_fcio.py source for static analysis."""
    return (_PROJECT_ROOT / "llm_fcio.py").read_text()


# ── custom-exceptions ─────────────────────────────────────────


class TestCustomExceptions:
    """Contract: ModelError and ApiError as distinct programmatic exception types."""

    @pytest.mark.xfail(reason="spec:quality-elevation custom-exceptions")
    def test_model_error_inherits_exception(self) -> None:
        assert issubclass(llm_fcio.ModelError, Exception)
        assert not issubclass(llm_fcio.ModelError, click.ClickException)

    @pytest.mark.xfail(reason="spec:quality-elevation custom-exceptions")
    def test_api_error_inherits_exception(self) -> None:
        assert issubclass(llm_fcio.ApiError, Exception)
        assert not issubclass(llm_fcio.ApiError, click.ClickException)

    @pytest.mark.xfail(reason="spec:quality-elevation custom-exceptions")
    def test_api_error_has_status_code_attribute(self) -> None:
        err = llm_fcio.ApiError("test", status_code=418)
        assert err.status_code == 418

    @pytest.mark.xfail(reason="spec:quality-elevation custom-exceptions")
    def test_api_error_status_code_defaults_none(self) -> None:
        err = llm_fcio.ApiError("test")
        assert err.status_code is None

    @pytest.mark.xfail(reason="spec:quality-elevation custom-exceptions")
    def test_exception_names_no_rzob_prefix(self) -> None:
        assert not llm_fcio.ModelError.__name__.startswith("Rzob")
        assert not llm_fcio.ApiError.__name__.startswith("Rzob")

    @pytest.mark.xfail(reason="spec:quality-elevation custom-exceptions")
    def test_model_error_used_for_model_resolution(self) -> None:
        """Model resolution errors must raise ModelError, not ClickException."""
        src = _read_source()
        # _resolve_model raises ModelError for ambiguous/unknown models
        assert "raise ModelError(" in src
        # Verify at least 2 ModelError raises exist (ambiguous + unknown)
        assert src.count("raise ModelError(") >= 2

    @pytest.mark.xfail(reason="spec:quality-elevation custom-exceptions")
    def test_api_error_used_for_api_failures(self) -> None:
        """API communication errors must raise ApiError."""
        src = _read_source()
        # api_request raises ApiError for status errors
        assert "raise ApiError(" in src
        # _extract_content raises ApiError for empty response
        assert 'ApiError("Empty response' in src

    @pytest.mark.xfail(reason="spec:quality-elevation custom-exceptions")
    def test_cli_validation_uses_click_exception(self) -> None:
        """User-facing validation errors remain click.ClickException."""
        src = _read_source()
        # Missing prompt, path not found, no files, aborted are CLI-user-facing
        assert (
            'click.ClickException("No files found' in src or 'ClickException("No files found' in src
        )
        assert 'click.ClickException("Aborted")' in src or 'ClickException("Aborted")' in src


# ── ruff-strict-config ─────────────────────────────────────────


class TestRuffStrictConfig:
    """Contract: ruff configured with explicit rule selection."""

    @pytest.mark.xfail(reason="spec:quality-elevation ruff-strict-config")
    def test_ruff_target_version_py314(self) -> None:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        assert cfg["tool"]["ruff"]["target-version"] == "py314"

    @pytest.mark.xfail(reason="spec:quality-elevation ruff-strict-config")
    def test_ruff_line_length_100(self) -> None:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        assert cfg["tool"]["ruff"]["line-length"] == 100

    @pytest.mark.xfail(reason="spec:quality-elevation ruff-strict-config")
    def test_ruff_lint_select_includes_ble(self) -> None:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        select = cfg["tool"]["ruff"]["lint"]["select"]
        assert "BLE" in select

    @pytest.mark.xfail(reason="spec:quality-elevation ruff-strict-config")
    def test_ruff_lint_select_includes_b904(self) -> None:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        select = cfg["tool"]["ruff"]["lint"]["select"]
        assert "B904" in select

    @pytest.mark.xfail(reason="spec:quality-elevation ruff-strict-config")
    def test_ruff_lint_ignores_e501(self) -> None:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        ignore = cfg["tool"]["ruff"]["lint"]["ignore"]
        assert "E501" in ignore

    @pytest.mark.xfail(reason="spec:quality-elevation ruff-strict-config")
    def test_ruff_format_quote_style_double(self) -> None:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        assert cfg["tool"]["ruff"]["format"]["quote-style"] == "double"

    @pytest.mark.xfail(reason="spec:quality-elevation ruff-strict-config")
    def test_ruff_lint_select_includes_core_rules(self) -> None:
        """Core rules E, F, I, N, W, UP, B, C4, SIM, TCH must be selected."""
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        select = cfg["tool"]["ruff"]["lint"]["select"]
        for rule in ("E", "F", "I", "N", "W", "UP", "B", "C4", "SIM", "TCH"):
            assert rule in select, f"Missing ruff rule: {rule}"


# ── type-annotations ──────────────────────────────────────────


class TestTypeAnnotations:
    """Contract: all public functions have return and parameter type annotations."""

    @pytest.mark.xfail(reason="spec:quality-elevation type-annotations")
    def test_get_api_key_has_return_annotation(self) -> None:
        sig = inspect.signature(llm_fcio.get_api_key)
        assert sig.return_annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation type-annotations")
    def test_api_request_has_return_annotation(self) -> None:
        sig = inspect.signature(llm_fcio.api_request)
        assert sig.return_annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation type-annotations")
    def test_build_messages_has_return_annotation(self) -> None:
        sig = inspect.signature(llm_fcio._build_messages)
        assert sig.return_annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation type-annotations")
    def test_discover_files_has_return_annotation(self) -> None:
        sig = inspect.signature(llm_fcio._discover_files)
        assert sig.return_annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation type-annotations")
    def test_chunk_lines_has_return_annotation(self) -> None:
        sig = inspect.signature(llm_fcio._chunk_lines)
        assert sig.return_annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation type-annotations")
    def test_make_client_has_return_annotation(self) -> None:
        sig = inspect.signature(llm_fcio._make_client)
        assert sig.return_annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation type-annotations")
    def test_auth_headers_has_return_annotation(self) -> None:
        sig = inspect.signature(llm_fcio._auth_headers)
        assert sig.return_annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation type-annotations")
    def test_extract_content_has_return_annotation(self) -> None:
        sig = inspect.signature(llm_fcio._extract_content)
        assert sig.return_annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation type-annotations")
    def test_iter_sse_content_has_return_annotation(self) -> None:
        sig = inspect.signature(llm_fcio._iter_sse_content)
        assert sig.return_annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation type-annotations")
    def test_public_api_functions_have_return_types(self) -> None:
        """All public API functions must have return type annotations."""
        public_funcs = [
            llm_fcio.refresh_models,
            llm_fcio.list_models,
            llm_fcio.get_model_info,
            llm_fcio.get_cached_models,
            llm_fcio.get_capabilities,
            llm_fcio.estimate_tokens,
            llm_fcio.ingest_files,
            llm_fcio.analyze_code,
            llm_fcio.collect_code_files,
        ]
        for func in public_funcs:
            sig = inspect.signature(func)
            assert sig.return_annotation is not inspect.Parameter.empty, (
                f"{func.__name__} missing return type annotation"
            )

    @pytest.mark.xfail(reason="spec:quality-elevation type-annotations")
    def test_api_request_parameters_have_types(self) -> None:
        sig = inspect.signature(llm_fcio.api_request)
        for pname, param in sig.parameters.items():
            assert param.annotation is not inspect.Parameter.empty, (
                f"api_request parameter '{pname}' missing type annotation"
            )

    @pytest.mark.xfail(reason="spec:quality-elevation type-annotations")
    def test_build_messages_parameters_have_types(self) -> None:
        sig = inspect.signature(llm_fcio._build_messages)
        for pname, param in sig.parameters.items():
            assert param.annotation is not inspect.Parameter.empty, (
                f"_build_messages parameter '{pname}' missing type annotation"
            )


# ── test-infrastructure ───────────────────────────────────────


class TestTestInfrastructure:
    """Contract: pytest-ready test infrastructure exists."""

    @pytest.mark.xfail(reason="spec:quality-elevation test-infrastructure")
    def test_tests_directory_exists(self) -> None:
        assert (_PROJECT_ROOT / "tests").is_dir()

    @pytest.mark.xfail(reason="spec:quality-elevation test-infrastructure")
    def test_conftest_exists(self) -> None:
        assert (_PROJECT_ROOT / "tests" / "conftest.py").is_file()

    @pytest.mark.xfail(reason="spec:quality-elevation test-infrastructure")
    def test_pytest_in_dev_dependencies(self) -> None:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        dev_deps = cfg["dependency-groups"]["dev"]
        dep_names = list(dev_deps)
        assert any("pytest" in d for d in dep_names)

    @pytest.mark.xfail(reason="spec:quality-elevation test-infrastructure")
    def test_pytest_cov_in_dev_dependencies(self) -> None:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        dev_deps = cfg["dependency-groups"]["dev"]
        dep_names = list(dev_deps)
        assert any("pytest-cov" in d for d in dep_names)

    @pytest.mark.xfail(reason="spec:quality-elevation test-infrastructure")
    def test_pytest_ini_options_in_pyproject(self) -> None:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        assert "tool" in cfg
        assert "pytest" in cfg["tool"]
        assert "ini_options" in cfg["tool"]["pytest"]

    @pytest.mark.xfail(reason="spec:quality-elevation test-infrastructure")
    def test_pytest_testpaths_configured(self) -> None:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        assert cfg["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]

    @pytest.mark.xfail(reason="spec:quality-elevation test-infrastructure")
    def test_pytest_cov_addopts(self) -> None:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        addopts = cfg["tool"]["pytest"]["ini_options"]["addopts"]
        opt_str = " ".join(addopts) if isinstance(addopts, list) else str(addopts)
        assert "--cov" in opt_str


# ── nesting-reduction ──────────────────────────────────────────


class TestNestingReduction:
    """Contract: SSE parsing logic extracted into shared _iter_sse_content."""

    @pytest.mark.xfail(reason="spec:quality-elevation nesting-reduction")
    def test_iter_sse_content_is_callable(self) -> None:
        assert callable(llm_fcio._iter_sse_content)

    @pytest.mark.xfail(reason="spec:quality-elevation nesting-reduction")
    def test_iter_sse_content_returns_tuple(self) -> None:
        """_iter_sse_content returns (metadata, iterator) tuple."""
        sig = inspect.signature(llm_fcio._iter_sse_content)
        ret = sig.return_annotation
        # Return annotation must mention tuple
        ret_str = str(ret)
        assert "tuple" in ret_str

    @pytest.mark.xfail(reason="spec:quality-elevation nesting-reduction")
    def test_execute_uses_iter_sse_content(self) -> None:
        """RzobModel.execute must delegate to _iter_sse_content."""
        src = _read_source()
        # Find the execute method body — it should call _iter_sse_content
        assert "_iter_sse_content" in src

    @pytest.mark.xfail(reason="spec:quality-elevation nesting-reduction")
    def test_sse_metadata_dataclass_exists(self) -> None:
        assert hasattr(llm_fcio, "_SSEMetadata")


# ── complexity-gate ────────────────────────────────────────────


class TestComplexityGate:
    """Contract: helper functions extracted from high-CC functions until CC<=15.

    All specific extractions from the spec decision.
    """

    # -- _build_messages extractions --

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_conversation_messages_extracted(self) -> None:
        """_conversation_messages(conversation) extracted from _build_messages."""
        assert hasattr(llm_fcio, "_conversation_messages")
        assert callable(llm_fcio._conversation_messages)

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_conversation_messages_has_annotations(self) -> None:
        sig = inspect.signature(llm_fcio._conversation_messages)
        assert sig.return_annotation is not inspect.Parameter.empty
        for _pname, param in sig.parameters.items():
            assert param.annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_build_user_content_extracted(self) -> None:
        """_build_user_content(prompt) extracted from _build_messages."""
        assert hasattr(llm_fcio, "_build_user_content")
        assert callable(llm_fcio._build_user_content)

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_build_user_content_has_annotations(self) -> None:
        sig = inspect.signature(llm_fcio._build_user_content)
        assert sig.return_annotation is not inspect.Parameter.empty
        for _pname, param in sig.parameters.items():
            assert param.annotation is not inspect.Parameter.empty

    # -- _make_client extractions --

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_make_request_hook_extracted(self) -> None:
        """_make_request_hook(debug_id, verbose) extracted from _make_client."""
        assert hasattr(llm_fcio, "_make_request_hook")
        assert callable(llm_fcio._make_request_hook)

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_make_request_hook_has_annotations(self) -> None:
        sig = inspect.signature(llm_fcio._make_request_hook)
        assert sig.return_annotation is not inspect.Parameter.empty
        for _pname, param in sig.parameters.items():
            assert param.annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_make_response_hook_extracted(self) -> None:
        """_make_response_hook(verbose) extracted from _make_client."""
        assert hasattr(llm_fcio, "_make_response_hook")
        assert callable(llm_fcio._make_response_hook)

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_make_response_hook_has_annotations(self) -> None:
        sig = inspect.signature(llm_fcio._make_response_hook)
        assert sig.return_annotation is not inspect.Parameter.empty
        for _pname, param in sig.parameters.items():
            assert param.annotation is not inspect.Parameter.empty

    # -- _iter_sse_content extractions --

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_parse_sse_event_extracted(self) -> None:
        """_parse_sse_event(data) extracted from _iter_sse_content."""
        assert hasattr(llm_fcio, "_parse_sse_event")
        assert callable(llm_fcio._parse_sse_event)

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_parse_sse_event_has_annotations(self) -> None:
        sig = inspect.signature(llm_fcio._parse_sse_event)
        assert sig.return_annotation is not inspect.Parameter.empty
        for _pname, param in sig.parameters.items():
            assert param.annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_handle_sse_event_extracted(self) -> None:
        """_handle_sse_event(event, meta) extracted from _iter_sse_content."""
        assert hasattr(llm_fcio, "_handle_sse_event")
        assert callable(llm_fcio._handle_sse_event)

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_handle_sse_event_has_annotations(self) -> None:
        sig = inspect.signature(llm_fcio._handle_sse_event)
        assert sig.return_annotation is not inspect.Parameter.empty
        for _pname, param in sig.parameters.items():
            assert param.annotation is not inspect.Parameter.empty

    # -- _stream_chat_response extraction --

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_render_or_echo_extracted(self) -> None:
        """_render_or_echo(content_iter, renderer) extracted from _stream_chat_response."""
        assert hasattr(llm_fcio, "_render_or_echo")
        assert callable(llm_fcio._render_or_echo)

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_render_or_echo_has_annotations(self) -> None:
        sig = inspect.signature(llm_fcio._render_or_echo)
        assert sig.return_annotation is not inspect.Parameter.empty
        for _pname, param in sig.parameters.items():
            assert param.annotation is not inspect.Parameter.empty

    # -- cmd_capabilities extractions --

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_format_capabilities_text_extracted(self) -> None:
        """_format_capabilities_text(result) extracted from cmd_capabilities."""
        assert hasattr(llm_fcio, "_format_capabilities_text")
        assert callable(llm_fcio._format_capabilities_text)

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_format_capabilities_text_has_annotations(self) -> None:
        sig = inspect.signature(llm_fcio._format_capabilities_text)
        assert sig.return_annotation is not inspect.Parameter.empty
        for _pname, param in sig.parameters.items():
            assert param.annotation is not inspect.Parameter.empty

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_print_models_section_at_module_level(self) -> None:
        """_print_models promoted to _print_models_section at module level."""
        assert hasattr(llm_fcio, "_print_models_section")
        assert callable(llm_fcio._print_models_section)

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_format_feature_status_at_module_level(self) -> None:
        """_status_icon promoted to _format_feature_status at module level."""
        assert hasattr(llm_fcio, "_format_feature_status")
        assert callable(llm_fcio._format_feature_status)

    @pytest.mark.xfail(reason="spec:quality-elevation complexity-gate")
    def test_cmd_capabilities_no_nested_def_print_models(self) -> None:
        """cmd_capabilities must not contain nested _print_models def."""
        src = _read_source()
        # Find cmd_capabilities body — there should be no nested def _print_models
        # Look for the pattern inside cmd_capabilities
        in_cmd_caps = False
        for line in src.splitlines():
            stripped = line.strip()
            if "def cmd_capabilities" in stripped:
                in_cmd_caps = True
                base_indent = len(line) - len(line.lstrip())
                continue
            if in_cmd_caps and stripped and not stripped.startswith("#"):
                current_indent = len(line) - len(line.lstrip())
                if current_indent <= base_indent and stripped.startswith("def "):
                    break  # reached next top-level function
                if "def _print_models" in stripped:
                    pytest.fail("cmd_capabilities still contains nested _print_models")
                if "def _status_icon" in stripped:
                    pytest.fail("cmd_capabilities still contains nested _status_icon")


# ── dead-code-forwarding ──────────────────────────────────────


class TestDeadCodeForwarding:
    """Contract: tools and response_format forwarded in execute()."""

    @pytest.mark.xfail(reason="spec:quality-elevation dead-code-forwarding")
    def test_tools_forwarded_in_execute(self) -> None:
        """execute() must forward prompt.options.tools to body['tools']."""
        src = _read_source()
        # Find execute method and check for tools forwarding
        assert 'body["tools"]' in src
        assert "prompt.options.tools" in src

    @pytest.mark.xfail(reason="spec:quality-elevation dead-code-forwarding")
    def test_response_format_forwarded_in_execute(self) -> None:
        """execute() must forward prompt.options.response_format to body['response_format']."""
        src = _read_source()
        assert 'body["response_format"]' in src
        assert "prompt.options.response_format" in src

    @pytest.mark.xfail(reason="spec:quality-elevation dead-code-forwarding")
    def test_tools_forwarding_after_top_p(self) -> None:
        """tools forwarding must appear after top_p forwarding in execute."""
        src = _read_source()
        top_p_pos = src.find('body["top_p"]')
        tools_pos = src.find('body["tools"]')
        assert top_p_pos > 0, "top_p forwarding not found"
        assert tools_pos > top_p_pos, "tools must be forwarded after top_p"

    @pytest.mark.xfail(reason="spec:quality-elevation dead-code-forwarding")
    def test_options_declare_tools_and_response_format(self) -> None:
        """RzobModel.Options must declare tools and response_format fields."""
        assert hasattr(llm_fcio.RzobModel, "Options")
        # Check the fields exist via pydantic model_fields
        opt_fields = llm_fcio.RzobModel.Options.model_fields
        assert "tools" in opt_fields
        assert "response_format" in opt_fields


# ── subprocess-hardening ──────────────────────────────────────


class TestSubprocessHardening:
    """Contract: fzf subprocess uses shutil.which, check=False, timeout."""

    @pytest.mark.xfail(reason="spec:quality-elevation subprocess-hardening")
    def test_shutil_imported(self) -> None:
        """shutil must be imported in the module."""
        src = _read_source()
        assert "import shutil" in src

    @pytest.mark.xfail(reason="spec:quality-elevation subprocess-hardening")
    def test_shutil_which_used_before_subprocess(self) -> None:
        """shutil.which('fzf') must pre-check fzf availability."""
        src = _read_source()
        assert 'shutil.which("fzf")' in src or "shutil.which('fzf')" in src

    @pytest.mark.xfail(reason="spec:quality-elevation subprocess-hardening")
    def test_subprocess_check_false(self) -> None:
        """subprocess.run must use check=False."""
        src = _read_source()
        assert "check=False" in src

    @pytest.mark.xfail(reason="spec:quality-elevation subprocess-hardening")
    def test_subprocess_timeout_present(self) -> None:
        """subprocess.run must include timeout=10."""
        src = _read_source()
        assert "timeout=10" in src

    @pytest.mark.xfail(reason="spec:quality-elevation subprocess-hardening")
    def test_fzf_path_used_in_subprocess(self) -> None:
        """subprocess.run must use the resolved fzf_path, not bare 'fzf'."""
        src = _read_source()
        # Should use fzf_path variable from shutil.which, not string "fzf"
        # The pattern should be [fzf_path, ...] not ["fzf", ...]
        assert "fzf_path" in src
        # Must NOT have subprocess.run(["fzf" — that's the S607 violation
        assert 'subprocess.run(["fzf"' not in src


# ── httpx-status-codes ────────────────────────────────────────


class TestHttpxStatusCodes:
    """Contract: named httpx status code constants replace raw integers."""

    @pytest.mark.xfail(reason="spec:quality-elevation httpx-status-codes")
    def test_httpx_codes_used_for_threshold(self) -> None:
        """Status threshold check must use httpx.codes.BAD_REQUEST, not raw 400."""
        src = _read_source()
        assert "httpx.codes.BAD_REQUEST" in src

    @pytest.mark.xfail(reason="spec:quality-elevation httpx-status-codes")
    def test_httpx_codes_unauthorized_used(self) -> None:
        """401 checks must use httpx.codes.UNAUTHORIZED."""
        src = _read_source()
        assert "httpx.codes.UNAUTHORIZED" in src

    @pytest.mark.xfail(reason="spec:quality-elevation httpx-status-codes")
    def test_no_string_match_status_hack(self) -> None:
        """No string-match hacks like '400' in str(e) for status code checks."""
        src = _read_source()
        # This pattern is specifically called out as a hack to eliminate
        assert '"400" in str(e)' not in src
        assert "'400' in str(e)" not in src

    @pytest.mark.xfail(reason="spec:quality-elevation httpx-status-codes")
    def test_httpx_codes_not_found_used(self) -> None:
        """404 comparisons must use httpx.codes.NOT_FOUND or be replaced."""
        src = _read_source()
        # get_model_info checks for 404
        # The spec says use httpx.codes.NOT_FOUND
        assert "httpx.codes.NOT_FOUND" in src or "status_code == 404" not in src

    @pytest.mark.xfail(reason="spec:quality-elevation httpx-status-codes")
    def test_no_bare_400_in_comparisons(self) -> None:
        """No raw 400 in status code comparisons (>= 400 becomes >= httpx.codes.BAD_REQUEST)."""
        src = _read_source()
        # Check that >= 400 comparisons use named constant
        bare_400_pattern = re.compile(r">=\s*400\b")
        # Find all matches — should be none if using named constant
        matches = bare_400_pattern.findall(src)
        assert len(matches) == 0, f"Found bare >= 400 comparisons: {matches}"
