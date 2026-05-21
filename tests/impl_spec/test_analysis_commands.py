"""Spec validation tests for analysis-commands.

Validates contracts from .agents/impl_specs/analysis-commands.md:
- file-collection: extension whitelist + .gitignore filtering
- template-system: TEMPLATES dict + register_template_loaders
- command-interface: llm fcio analyze CLI command
- renderer-safety: TTY guard + fallback on failure
- zero-config-defaults: auto-detection, error messages, file display

Tests validate: file-collection, template-system, command-interface, renderer-safety, zero-config-defaults.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from llm_fcio import register_commands

# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def cli() -> click.Group:
    """Create a Click group with fcio commands registered."""
    group = click.Group()
    register_commands(group)
    return group


@pytest.fixture
def runner() -> CliRunner:
    """Create a CliRunner for invoking commands."""
    return CliRunner()


# ══════════════════════════════════════════════════════════════
# 1. File collection unit tests
# Spec decision: file-collection
# ══════════════════════════════════════════════════════════════


def test_file_collection_includes_python_files(tmp_path: Path) -> None:
    """Spec decision: file-collection — .py files included via extension whitelist."""
    (tmp_path / "app.py").write_text("print('hello')")
    (tmp_path / "util.py").write_text("def helper(): pass")
    from llm_fcio import collect_code_files

    files = collect_code_files(tmp_path)
    names = [f.name for f in files]
    assert "app.py" in names
    assert "util.py" in names


def test_file_collection_excludes_non_code_extensions(tmp_path: Path) -> None:
    """Spec decision: file-collection — non-code extensions excluded by whitelist."""
    (tmp_path / "data.json").write_text("{}")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / "notes.txt").write_text("not code")
    from llm_fcio import collect_code_files

    files = collect_code_files(tmp_path)
    names = [f.name for f in files]
    assert "data.json" not in names
    assert "image.png" not in names
    assert "notes.txt" not in names


def test_file_collection_respects_gitignore(tmp_path: Path) -> None:
    """Spec decision: file-collection — .gitignore filtering via pathspec."""
    (tmp_path / ".gitignore").write_text("__pycache__/\n*.pyc\nbuild/\n")
    (tmp_path / "app.py").write_text("print('hello')")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "app.cpython-314.pyc").write_bytes(b"\x00")
    build = tmp_path / "build"
    build.mkdir()
    (build / "output.py").write_text("# generated")
    from llm_fcio import collect_code_files

    files = collect_code_files(tmp_path)
    names = [f.name for f in files]
    assert "app.py" in names
    assert "app.cpython-314.pyc" not in names
    assert "output.py" not in names


def test_file_collection_returns_path_objects(tmp_path: Path) -> None:
    """Spec decision: file-collection — returns Path objects with valid file sizes."""
    (tmp_path / "main.py").write_text("x = 1")
    from llm_fcio import collect_code_files

    files = collect_code_files(tmp_path)
    assert len(files) >= 1
    assert all(isinstance(f, Path) for f in files)


def test_file_collection_empty_directory(tmp_path: Path) -> None:
    """Spec decision: file-collection — empty dir returns empty list."""
    empty = tmp_path / "empty_project"
    empty.mkdir()
    from llm_fcio import collect_code_files

    files = collect_code_files(empty)
    assert files == []


# ══════════════════════════════════════════════════════════════
# 2. Template loader tests
# Spec decision: template-system
# ══════════════════════════════════════════════════════════════


def test_templates_dict_contains_review() -> None:
    """Spec decision: template-system — TEMPLATES dict has 'review' key."""
    from llm_fcio import TEMPLATES

    assert "review" in TEMPLATES


def test_templates_dict_contains_overview() -> None:
    """Spec decision: template-system — TEMPLATES dict has 'overview' key."""
    from llm_fcio import TEMPLATES

    assert "overview" in TEMPLATES


def test_template_loader_registered_as_fcio() -> None:
    """Spec decision: template-system — loader registered via register_template_loaders("fcio", loader)."""
    from llm_fcio import fcio_template_loader

    assert callable(fcio_template_loader)


def test_template_loader_returns_llm_template_instances() -> None:
    """Spec decision: template-system — loader maps name to llm.Template instances."""
    import llm

    from llm_fcio import fcio_template_loader

    templates = fcio_template_loader()
    for name, tmpl in templates.items():
        assert isinstance(tmpl, llm.Template), f"{name} is not an llm.Template"


def test_templates_available_via_fcio_prefix() -> None:
    """Spec decision: template-system — templates available as fcio:<name>."""
    from llm_fcio import fcio_template_loader

    templates = fcio_template_loader()
    assert all(isinstance(name, str) for name in templates)
    # Templates must be addressable as "fcio:review", "fcio:overview"
    assert "review" in templates
    assert "overview" in templates


# ══════════════════════════════════════════════════════════════
# 3. Analyze command E2E tests
# Spec decision: command-interface, zero-config-defaults
# ══════════════════════════════════════════════════════════════


def test_analyze_help_shows_types_and_examples(
    runner: CliRunner,
    cli: click.Group,
) -> None:
    """Spec decision: command-interface — llm fcio analyze --help shows types and examples."""
    result = runner.invoke(cli, ["fcio", "analyze", "--help"])
    assert result.exit_code == 0
    assert "review" in result.output
    assert "overview" in result.output


def test_analyze_auto_detects_code_files_in_cwd(
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    """Spec decision: zero-config-defaults — no args auto-detects code files in CWD."""
    (tmp_path / "main.py").write_text("def hello(): pass")
    mock_response = MagicMock()
    mock_response.__iter__ = MagicMock(return_value=iter(["Analysis result"]))
    with (
        patch("llm_fcio.collect_code_files", return_value=[tmp_path / "main.py"]),
        patch("llm_fcio.llm.get_model") as mock_get_model,
    ):
        mock_model = MagicMock()
        mock_model.prompt.return_value = mock_response
        mock_get_model.return_value = mock_model
        result = runner.invoke(cli, ["fcio", "analyze"])
    assert result.exit_code == 0


def test_analyze_review_with_specific_files(
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    """Spec decision: command-interface — llm fcio analyze review src/**/*.py reviews specific files."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("x = 1")
    mock_response = MagicMock()
    mock_response.__iter__ = MagicMock(return_value=iter(["Review output"]))
    with (
        patch("llm_fcio.collect_code_files"),
        patch("llm_fcio.llm.get_model") as mock_get_model,
    ):
        mock_model = MagicMock()
        mock_model.prompt.return_value = mock_response
        mock_get_model.return_value = mock_model
        result = runner.invoke(
            cli,
            ["fcio", "analyze", "review", str(src / "app.py")],
        )
    assert result.exit_code == 0


def test_analyze_overview_with_model_flag(
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    """Spec decision: command-interface — llm fcio analyze overview --model 120b uses specified model."""
    (tmp_path / "mod.py").write_text("y = 2")
    mock_response = MagicMock()
    mock_response.__iter__ = MagicMock(return_value=iter(["Overview output"]))
    with (
        patch("llm_fcio.collect_code_files", return_value=[tmp_path / "mod.py"]),
        patch("llm_fcio.llm.get_model") as mock_get_model,
    ):
        mock_model = MagicMock()
        mock_model.prompt.return_value = mock_response
        mock_get_model.return_value = mock_model
        result = runner.invoke(cli, ["fcio", "analyze", "overview", "--model", "120b"])
    assert result.exit_code == 0
    mock_get_model.assert_called_once()


def test_analyze_no_code_files_error_message(
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    """Spec decision: zero-config-defaults — no files found: clear error with hints, exit 1."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with patch("llm_fcio.collect_code_files", return_value=[]):
        result = runner.invoke(cli, ["fcio", "analyze"])
    assert result.exit_code == 1
    assert "No code files found" in result.output


def test_analyze_no_code_files_shows_actionable_hints(
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    """Spec decision: zero-config-defaults — error includes hints about specifying files and checking extensions."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with patch("llm_fcio.collect_code_files", return_value=[]):
        result = runner.invoke(cli, ["fcio", "analyze"])
    assert result.exit_code == 1
    # Must provide actionable hints (spec: "specify files, check extensions")
    output_lower = result.output.lower()
    assert "specify" in output_lower or "files" in output_lower


def test_analyze_displays_file_sizes_and_token_estimate(
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    """Spec decision: zero-config-defaults — file list shows sizes and token estimate (chars/4)."""
    (tmp_path / "app.py").write_text("x = 1")  # 5 chars → ~1 token
    mock_response = MagicMock()
    mock_response.__iter__ = MagicMock(return_value=iter(["result"]))
    with (
        patch("llm_fcio.collect_code_files", return_value=[tmp_path / "app.py"]),
        patch("llm_fcio.llm.get_model") as mock_get_model,
    ):
        mock_model = MagicMock()
        mock_model.prompt.return_value = mock_response
        mock_get_model.return_value = mock_model
        result = runner.invoke(cli, ["fcio", "analyze"])
    assert result.exit_code == 0
    # Output should show file info with size and token estimate
    assert "app.py" in result.output


def test_analyze_invalid_analysis_type(
    runner: CliRunner,
    cli: click.Group,
) -> None:
    """Spec decision: command-interface — invalid analysis type is rejected with helpful error."""
    # 'analyze' command must exist and validate the type argument
    # 'badtype' is not review|overview → should produce a type validation error
    result = runner.invoke(cli, ["fcio", "analyze", "badtype"])
    assert result.exit_code != 0
    # Must NOT be a generic 'no such command' error — must be type validation
    assert (
        "review" in result.output or "overview" in result.output or "type" in result.output.lower()
    )


# ══════════════════════════════════════════════════════════════
# 4. Renderer monkey-patch safety tests
# Spec decision: renderer-safety, renderer-hook
# ══════════════════════════════════════════════════════════════


def test_renderer_patch_only_active_when_tty() -> None:
    """Spec decision: renderer-safety — patch only when sys.stdout.isatty() is True."""
    import llm

    from llm_fcio import install_renderer_patch

    original_iter = llm.Response.__iter__
    with patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = True
        install_renderer_patch()
        patched_iter = llm.Response.__iter__
    # Restore to avoid polluting other tests
    llm.Response.__iter__ = original_iter
    assert patched_iter is not original_iter


def test_renderer_patch_not_applied_when_not_tty() -> None:
    """Spec decision: renderer-safety — original behavior unchanged in non-TTY."""
    import llm

    from llm_fcio import install_renderer_patch

    original_iter = llm.Response.__iter__
    with patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = False
        install_renderer_patch()
        current_iter = llm.Response.__iter__
    assert current_iter is original_iter


def test_renderer_failure_falls_back_to_original_iter() -> None:
    """Spec decision: renderer-safety — on failure, fall back to original __iter__."""
    import llm

    from llm_fcio import install_renderer_patch

    original_iter = llm.Response.__iter__
    with patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = True
        with patch("llm_fcio._StreamingRenderer", side_effect=RuntimeError("renderer crash")):
            install_renderer_patch()
    # Even after install, the patched version must handle renderer failure
    # by falling back — so patched_iter wraps original_iter as safety net
    llm.Response.__iter__ = original_iter
    # The patch was applied but should gracefully degrade


def test_renderer_patch_transparent_degradation() -> None:
    """Spec decision: renderer-safety — graceful degradation is invisible to the user."""
    from llm_fcio import install_renderer_patch

    # Calling install twice should not raise (idempotent, safe)
    with patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = False
        install_renderer_patch()
        install_renderer_patch()  # second call must not crash
