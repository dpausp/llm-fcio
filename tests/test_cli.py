"""CLI tests for llm_fcio commands using Click's CliRunner.

Covers all 8 fcio subcommands: refresh, models, chat, embed,
capabilities, simulate, tokens, ingest.
HTTP-boundary mocking via respx; only non-HTTP code runs unmocked.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import httpx
import pytest
import respx
from click.testing import CliRunner

from llm_fcio import register_commands

API_BASE = "https://ai.rzob.fcio.net/openai/v1"
MODELS_URL = f"{API_BASE}/models"
CHAT_URL = f"{API_BASE}/chat/completions"
EMBED_URL = f"{API_BASE}/embeddings"
BASE_URL = "https://ai.rzob.fcio.net/openai"

SAMPLE_MODELS = [
    {"id": "gpt-oss:20b", "owned_by": "fcio"},
    {"id": "gpt-oss:120b", "owned_by": "fcio"},
    {"id": "bge-m3:567m", "owned_by": "fcio"},
]


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


def _setup_models_route() -> None:
    """Register a respx mock route for GET /models."""
    respx.get(MODELS_URL).mock(
        return_value=httpx.Response(200, json={"data": SAMPLE_MODELS}),
    )


def _setup_chat_route(content: str = "Hello!") -> None:
    """Register a respx mock route for POST /chat/completions."""
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
            },
        ),
    )


def _setup_embed_route(count: int = 1) -> None:
    """Register a respx mock route for POST /embeddings."""
    data = [{"embedding": [0.1, 0.2, 0.3], "usage": {}} for _ in range(count)]
    respx.post(EMBED_URL).mock(
        return_value=httpx.Response(200, json={"data": data}),
    )


def _setup_capabilities_routes() -> None:
    """Register respx mock routes for all capabilities probes."""
    _setup_models_route()
    respx.get(BASE_URL).mock(return_value=httpx.Response(200))
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "model not found"}},
        ),
    )
    respx.post(EMBED_URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "model not found"}},
        ),
    )


# ── refresh ───────────────────────────────────────────────


@respx.mock
def test_refresh_success(runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
    cache_file = tmp_path / "models.json"
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("llm_fcio._cache_path", return_value=cache_file),
    ):
        _setup_models_route()
        result = runner.invoke(cli, ["fcio", "refresh"])
    assert result.exit_code == 0
    assert "Cached 3 models" in result.output
    assert cache_file.exists()


@respx.mock
def test_refresh_api_error(runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
    cache_file = tmp_path / "models.json"
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("llm_fcio._cache_path", return_value=cache_file),
    ):
        respx.get(MODELS_URL).mock(
            return_value=httpx.Response(401, json={"detail": "Unauthorized"}),
        )
        result = runner.invoke(cli, ["fcio", "refresh"])
    assert result.exit_code != 0
    assert result.exception is not None


@respx.mock
def test_refresh_empty_response(
    runner: CliRunner, cli: click.Group, tmp_path: Path,
) -> None:
    cache_file = tmp_path / "models.json"
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("llm_fcio._cache_path", return_value=cache_file),
    ):
        respx.get(MODELS_URL).mock(
            return_value=httpx.Response(200, json={"data": []}),
        )
        result = runner.invoke(cli, ["fcio", "refresh"])
    assert result.exit_code == 0
    assert "Cached 0 models" in result.output


# ── models ────────────────────────────────────────────────


@respx.mock
def test_models_with_data(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        result = runner.invoke(cli, ["fcio", "models"])
    assert result.exit_code == 0
    assert "gpt-oss:20b" in result.output
    assert "bge-m3:567m" in result.output
    assert "chat" in result.output
    assert "embed" in result.output


@respx.mock
def test_models_json_output(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        result = runner.invoke(cli, ["fcio", "models", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 3
    assert data[0]["id"] == "gpt-oss:20b"


@respx.mock
def test_models_with_filter(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        result = runner.invoke(cli, ["fcio", "models", "--filter", "bge"])
    assert result.exit_code == 0
    assert "bge-m3:567m" in result.output
    assert "gpt-oss" not in result.output


@respx.mock
def test_models_empty_response(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        respx.get(MODELS_URL).mock(
            return_value=httpx.Response(200, json={"data": []}),
        )
        result = runner.invoke(cli, ["fcio", "models"])
    assert result.exit_code == 0
    # Table header printed even when empty
    assert "Type" in result.output


# ── chat ──────────────────────────────────────────────────


@respx.mock
def test_chat_basic_no_stream(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        _setup_chat_route("Hello from AI!")
        result = runner.invoke(cli, ["fcio", "chat", "--no-stream", "Say hello"])
    assert result.exit_code == 0
    assert "Hello from AI!" in result.output


@respx.mock
def test_chat_with_model_option(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        _setup_chat_route("Response from 120b")
        result = runner.invoke(
            cli, ["fcio", "chat", "--no-stream", "-m", "gpt-oss:120b", "test"],
        )
    assert result.exit_code == 0
    assert "Response from 120b" in result.output


def test_chat_streaming_output(runner: CliRunner, cli: click.Group) -> None:
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("llm_fcio._resolve_model", return_value="gpt-oss:20b"),
        patch("llm_fcio._iter_sse_content", return_value=iter(["Hello", " world"])),
    ):
        result = runner.invoke(cli, ["fcio", "chat", "--no-markdown", "Say hello"])
    assert result.exit_code == 0
    assert "Hello world" in result.output


@respx.mock
def test_chat_no_prompt_error(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        result = runner.invoke(cli, ["fcio", "chat", "--no-stream"])
    assert result.exit_code != 0
    assert "Prompt required" in result.output


@respx.mock
def test_chat_api_error(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(500, json={"detail": "Internal Server Error"}),
        )
        result = runner.invoke(cli, ["fcio", "chat", "--no-stream", "test"])
    assert result.exit_code != 0
    assert result.exception is not None


# ── embed ─────────────────────────────────────────────────


@respx.mock
def test_embed_single_text(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_embed_route(count=1)
        result = runner.invoke(cli, ["fcio", "embed", "hello world"])
    assert result.exit_code == 0
    assert "Text 1:" in result.output
    assert "3 dims" in result.output


@respx.mock
def test_embed_multiple_texts(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_embed_route(count=2)
        result = runner.invoke(cli, ["fcio", "embed", "hello", "world"])
    assert result.exit_code == 0
    assert "Text 1:" in result.output
    assert "Text 2:" in result.output


@respx.mock
def test_embed_json_output(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_embed_route(count=1)
        result = runner.invoke(cli, ["fcio", "embed", "--json", "hello"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "data" in data


@respx.mock
def test_embed_api_error(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        respx.post(EMBED_URL).mock(
            return_value=httpx.Response(500, json={"detail": "Server Error"}),
        )
        result = runner.invoke(cli, ["fcio", "embed", "test"])
    assert result.exit_code != 0
    assert result.exception is not None


# ── capabilities ──────────────────────────────────────────


@respx.mock
def test_capabilities_healthy(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_capabilities_routes()
        result = runner.invoke(cli, ["fcio", "capabilities"])
    assert result.exit_code == 0
    assert "FCIO RZOB" in result.output
    assert "reachable" in result.output


@respx.mock
def test_capabilities_json_output(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_capabilities_routes()
        result = runner.invoke(cli, ["fcio", "capabilities", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "endpoint" in data
    assert "models" in data
    assert "features" in data
    assert data["endpoint"]["name"] == "rzob"


# ── simulate ──────────────────────────────────────────────


@patch("llm_fcio.time.sleep")
def test_simulate_raw(
    mock_sleep: MagicMock, runner: CliRunner, cli: click.Group,
) -> None:
    result = runner.invoke(cli, ["fcio", "simulate", "--raw"])
    assert result.exit_code == 0
    assert "Python Decorators" in result.output
    assert "my_decorator" in result.output
    assert "```python" in result.output


@patch("llm_fcio.time.sleep")
def test_simulate_default(
    mock_sleep: MagicMock, runner: CliRunner, cli: click.Group,
) -> None:
    result = runner.invoke(cli, ["fcio", "simulate"])
    assert result.exit_code == 0
    assert len(result.output) > 0


@patch("llm_fcio.time.sleep")
def test_simulate_fast_speed(
    mock_sleep: MagicMock, runner: CliRunner, cli: click.Group,
) -> None:
    result = runner.invoke(cli, ["fcio", "simulate", "--speed", "fast", "--raw"])
    assert result.exit_code == 0
    assert "Python Decorators" in result.output
    # Same seed → same output
    result2 = runner.invoke(cli, ["fcio", "simulate", "--speed", "fast", "--raw"])
    assert result2.output == result.output


# ── tokens ────────────────────────────────────────────────


@respx.mock
def test_tokens_basic(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": ""}}],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 1,
                        "total_tokens": 11,
                    },
                },
            ),
        )
        result = runner.invoke(cli, ["fcio", "tokens", "hello world"])
    assert result.exit_code == 0
    assert "Tokens:" in result.output
    assert "10" in result.output


@respx.mock
def test_tokens_json(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": ""}}],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 1,
                        "total_tokens": 6,
                    },
                },
            ),
        )
        result = runner.invoke(cli, ["fcio", "tokens", "--json", "test"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["prompt_tokens"] == 5


@respx.mock
def test_tokens_api_error_fallback(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(500, json={"detail": "Not supported"}),
        )
        result = runner.invoke(cli, ["fcio", "tokens", "hello world"])
    assert result.exit_code == 0
    assert "Rough estimate" in result.output
    assert "~2 tokens" in result.output


# ── ingest ────────────────────────────────────────────────


def test_ingest_no_files_found(
    runner: CliRunner, cli: click.Group, tmp_path: Path,
) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = runner.invoke(cli, ["fcio", "ingest", "testcol", str(empty_dir), "--yes"])
    assert result.exit_code != 0
    assert "No files found" in result.output


def test_ingest_path_not_found(
    runner: CliRunner, cli: click.Group, tmp_path: Path,
) -> None:
    missing = tmp_path / "nonexistent"
    result = runner.invoke(cli, ["fcio", "ingest", "testcol", str(missing), "--yes"])
    assert result.exit_code != 0
    assert "Path not found" in result.output


@patch("llm_fcio.llm.Collection")
@patch("llm_fcio.llm.user_dir")
def test_ingest_single_file(
    mock_user_dir: MagicMock,
    mock_collection_cls: MagicMock,
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    mock_user_dir.return_value = tmp_path
    mock_col = MagicMock()
    mock_col.model.return_value.model_id = "bge-m3-567m"
    mock_collection_cls.exists.return_value = False
    mock_collection_cls.return_value = mock_col

    doc = tmp_path / "test.md"
    doc.write_text("# Test\nHello world\nLine 3\nLine 4\n")

    result = runner.invoke(cli, ["fcio", "ingest", "testcol", str(doc), "--yes"])
    assert result.exit_code == 0
    assert "Ingested" in result.output
    mock_col.embed_multi.assert_called_once()


@patch("llm_fcio.llm.Collection")
@patch("llm_fcio.llm.user_dir")
def test_ingest_multiple_files(
    mock_user_dir: MagicMock,
    mock_collection_cls: MagicMock,
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    mock_user_dir.return_value = tmp_path
    mock_col = MagicMock()
    mock_col.model.return_value.model_id = "bge-m3-567m"
    mock_collection_cls.exists.return_value = False
    mock_collection_cls.return_value = mock_col

    doc1 = tmp_path / "a.md"
    doc2 = tmp_path / "b.md"
    doc1.write_text("File A content")
    doc2.write_text("File B content")

    result = runner.invoke(
        cli, ["fcio", "ingest", "testcol", str(doc1), str(doc2), "--yes"],
    )
    assert result.exit_code == 0
    assert "Ingested" in result.output
    assert "2 chunks" in result.output


@patch("llm_fcio.llm.Collection")
@patch("llm_fcio.llm.user_dir")
def test_ingest_chunk_options(
    mock_user_dir: MagicMock,
    mock_collection_cls: MagicMock,
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    mock_user_dir.return_value = tmp_path
    mock_col = MagicMock()
    mock_col.model.return_value.model_id = "bge-m3-567m"
    mock_collection_cls.exists.return_value = False
    mock_collection_cls.return_value = mock_col

    doc = tmp_path / "big.md"
    lines = [f"Line {i}" for i in range(100)]
    doc.write_text("\n".join(lines))

    result = runner.invoke(
        cli,
        [
            "fcio", "ingest", "testcol", str(doc),
            "--yes", "--chunk-size", "10", "--overlap", "2",
        ],
    )
    assert result.exit_code == 0
    assert "Ingested" in result.output
