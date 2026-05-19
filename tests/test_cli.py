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
            400,
            json={"error": {"message": "model not found"}},
        ),
    )
    respx.post(EMBED_URL).mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "model not found"}},
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
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
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


@respx.mock
def test_models_detail_valid(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        respx.get(f"{API_BASE}/models/test-model").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"id": "test-model", "owned_by": "test-org", "created": 12345}},
            ),
        )
        result = runner.invoke(cli, ["fcio", "models", "test-model"])
    assert result.exit_code == 0
    assert "Model: test-model" in result.output
    assert "Owner:  test-org" in result.output
    assert "12345" in result.output


@respx.mock
def test_models_detail_json(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        respx.get(f"{API_BASE}/models/test-model").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"id": "test-model", "owned_by": "test-org", "created": 12345}},
            ),
        )
        result = runner.invoke(cli, ["fcio", "models", "test-model", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["id"] == "test-model"
    assert data["owned_by"] == "test-org"


@respx.mock
def test_models_detail_not_found(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        respx.get(f"{API_BASE}/models/nonexistent").mock(
            return_value=httpx.Response(
                404,
                json={"detail": "Not found"},
            ),
        )
        result = runner.invoke(cli, ["fcio", "models", "nonexistent"])
    assert result.exit_code != 0
    assert "Model not found" in result.output


@respx.mock
def test_models_detail_missing_fields(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        respx.get(f"{API_BASE}/models/minimal").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"id": "minimal"}},
            ),
        )
        result = runner.invoke(cli, ["fcio", "models", "minimal"])
    assert result.exit_code == 0
    assert "Owner:  unknown" in result.output
    assert "Created: unknown" in result.output


# ── chat --interactive ──────────────────────────────────────


@respx.mock
def test_chat_interactive_one_message(runner: CliRunner, cli: click.Group) -> None:
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("llm_fcio._resolve_model", return_value="gpt-oss:20b"),
        patch("click.prompt", side_effect=["Hello", EOFError]),
    ):
        _setup_chat_route("Interactive reply!")
        result = runner.invoke(
            cli,
            ["fcio", "chat", "--interactive", "--no-stream", "--no-markdown", "-m", "gpt-oss:20b"],
        )
    assert result.exit_code == 0
    assert "Interactive chat with gpt-oss:20b" in result.output
    assert "Interactive reply!" in result.output
    assert "Goodbye!" in result.output


@respx.mock
def test_chat_interactive_empty_input_skipped(runner: CliRunner, cli: click.Group) -> None:
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("llm_fcio._resolve_model", return_value="gpt-oss:20b"),
        patch("click.prompt", side_effect=["", "Hello", EOFError]),
    ):
        _setup_chat_route("Reply")
        result = runner.invoke(
            cli,
            ["fcio", "chat", "--interactive", "--no-stream", "--no-markdown", "-m", "gpt-oss:20b"],
        )
    assert result.exit_code == 0
    assert "Reply" in result.output
    assert "Goodbye!" in result.output
    assert respx.post(CHAT_URL).call_count == 1


@respx.mock
def test_chat_interactive_immediate_eof(runner: CliRunner, cli: click.Group) -> None:
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("llm_fcio._resolve_model", return_value="gpt-oss:20b"),
        patch("click.prompt", side_effect=EOFError),
    ):
        result = runner.invoke(
            cli,
            ["fcio", "chat", "--interactive", "--no-stream", "-m", "gpt-oss:20b"],
        )
    assert result.exit_code == 0
    assert "Interactive chat with gpt-oss:20b" in result.output
    assert "Goodbye!" in result.output
    assert respx.post(CHAT_URL).called is False


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
            cli,
            ["fcio", "chat", "--no-stream", "-m", "gpt-oss:120b", "test"],
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
    assert "✅ available" in result.output


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
    mock_sleep: MagicMock,
    runner: CliRunner,
    cli: click.Group,
) -> None:
    result = runner.invoke(cli, ["fcio", "simulate", "--raw"])
    assert result.exit_code == 0
    assert "Python Decorators" in result.output
    assert "my_decorator" in result.output
    assert "```python" in result.output


@patch("llm_fcio.time.sleep")
def test_simulate_default(
    mock_sleep: MagicMock,
    runner: CliRunner,
    cli: click.Group,
) -> None:
    result = runner.invoke(cli, ["fcio", "simulate"])
    assert result.exit_code == 0
    assert len(result.output) > 0


@patch("llm_fcio.time.sleep")
def test_simulate_fast_speed(
    mock_sleep: MagicMock,
    runner: CliRunner,
    cli: click.Group,
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
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = runner.invoke(cli, ["fcio", "ingest", "testcol", str(empty_dir), "--yes"])
    assert result.exit_code != 0
    assert "No files found" in result.output


def test_ingest_path_not_found(
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
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
    # Cannot use spec=llm.Collection: llm is plugin-based, Collection resolves to MagicMock at import → InvalidSpecError
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
    # Cannot use spec=llm.Collection: llm is plugin-based, Collection resolves to MagicMock at import → InvalidSpecError
    mock_col = MagicMock()
    mock_col.model.return_value.model_id = "bge-m3-567m"
    mock_collection_cls.exists.return_value = False
    mock_collection_cls.return_value = mock_col

    doc1 = tmp_path / "a.md"
    doc2 = tmp_path / "b.md"
    doc1.write_text("File A content")
    doc2.write_text("File B content")

    result = runner.invoke(
        cli,
        ["fcio", "ingest", "testcol", str(doc1), str(doc2), "--yes"],
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
    # Cannot use spec=llm.Collection: llm is plugin-based, Collection resolves to MagicMock at import → InvalidSpecError
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
            "fcio",
            "ingest",
            "testcol",
            str(doc),
            "--yes",
            "--chunk-size",
            "10",
            "--overlap",
            "2",
        ],
    )
    assert result.exit_code == 0
    assert "Ingested" in result.output


# ── _make_client verbose/debug paths ────────────────────


@respx.mock
def test_make_client_verbose_fires_hooks() -> None:
    """verbose=True client fires request/response hooks without error."""
    from llm_fcio import _make_client

    route = respx.get("https://api.test.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []}),
    )
    client = _make_client(verbose=True, timeout=5.0)
    try:
        resp = client.get("https://api.test.com/v1/models")
        assert resp.status_code == 200
        assert route.called
    finally:
        client.close()


@respx.mock
def test_make_client_debug_sends_header() -> None:
    """debug=True client sends X-Skvaider-Debug-ID header."""
    from llm_fcio import _make_client

    route = respx.get("https://api.test.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []}),
    )
    client = _make_client(debug=True, timeout=5.0)
    try:
        client.get("https://api.test.com/v1/models")
        request, _ = route.calls.last
        assert "x-skvaider-debug-id" in request.headers
        # LID format: XXXXXXXXX-XXXX (base32-crockford, 14 chars total)
        debug_id = request.headers["x-skvaider-debug-id"]
        assert len(debug_id) == 14
        assert debug_id[9] == "-"
    finally:
        client.close()


@respx.mock
def test_make_client_verbose_with_json_body() -> None:
    """verbose=True handles request with JSON content and JSON response body."""
    from llm_fcio import _make_client

    route = respx.post("https://api.test.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Hi"}}]},
        ),
    )
    client = _make_client(verbose=True, timeout=5.0)
    try:
        resp = client.post(
            "https://api.test.com/v1/chat/completions",
            json={"model": "gpt-oss:20b", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
        assert route.called
    finally:
        client.close()


@respx.mock
def test_make_client_verbose_sse_response() -> None:
    """verbose=True handles SSE content-type response without error."""
    from llm_fcio import _make_client

    sse_body = 'data: {"content": "hello"}\n\n'
    route = respx.get("https://api.test.com/v1/stream").mock(
        return_value=httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"content-type": "text/event-stream"},
        ),
    )
    client = _make_client(verbose=True, timeout=5.0)
    try:
        resp = client.get("https://api.test.com/v1/stream")
        assert resp.status_code == 200
        assert route.called
    finally:
        client.close()


@respx.mock
def test_make_client_verbose_and_debug_combined() -> None:
    """Both verbose=True and debug=True active simultaneously."""
    from llm_fcio import _make_client

    route = respx.post("https://api.test.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        ),
    )
    client = _make_client(verbose=True, debug=True, timeout=5.0)
    try:
        resp = client.post(
            "https://api.test.com/v1/chat/completions",
            json={"model": "test", "messages": []},
        )
        assert resp.status_code == 200
        request, _ = route.calls.last
        assert "x-skvaider-debug-id" in request.headers
        # LID format: XXXXXXXXX-XXXX (base32-crockford, 14 chars total)
        debug_id = request.headers["x-skvaider-debug-id"]
        assert len(debug_id) == 14
        assert debug_id[9] == "-"
    finally:
        client.close()


# ── fcio group no subcommand ──────────────────────────────


def test_fcio_no_subcommand_shows_help(runner: CliRunner, cli: click.Group) -> None:
    """Invoking fcio with no subcommand shows help text."""
    result = runner.invoke(cli, ["fcio"])
    assert result.exit_code == 0
    assert "Commands for the FCIO AI platform" in result.output


# ── chat --system ──────────────────────────────────────────


@respx.mock
def test_chat_with_system_prompt(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        _setup_chat_route("System test reply")
        result = runner.invoke(
            cli,
            ["fcio", "chat", "--no-stream", "--no-markdown", "-s", "You are helpful", "test"],
        )
    assert result.exit_code == 0
    assert "System test reply" in result.output


# ── embed --dimensions ──────────────────────────────────────


@respx.mock
def test_embed_with_dimensions(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_embed_route(count=1)
        result = runner.invoke(cli, ["fcio", "embed", "-d", "256", "hello"])
    assert result.exit_code == 0
    assert "Text 1:" in result.output


# ── capabilities auth error path ──────────────────────────


@respx.mock
def test_capabilities_auth_failure(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        respx.get(MODELS_URL).mock(
            return_value=httpx.Response(401, json={"detail": "Unauthorized"}),
        )
        # Feature probes still run after auth failure
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(401, json={"detail": "Unauthorized"}),
        )
        respx.post(EMBED_URL).mock(
            return_value=httpx.Response(401, json={"detail": "Unauthorized"}),
        )
        result = runner.invoke(cli, ["fcio", "capabilities"])
    assert result.exit_code == 0
    assert "\u274c" in result.output  # \u274c


# ── capabilities with other_models ────────────────────────


@respx.mock
def test_capabilities_other_models_category(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        mixed_models = [
            {"id": "gpt-oss:20b", "owned_by": "fcio"},
            {"id": "bge-m3:567m", "owned_by": "fcio"},
            {"id": "whisper-large:1b", "owned_by": "fcio", "created": 12345},
        ]
        respx.get(MODELS_URL).mock(
            return_value=httpx.Response(200, json={"data": mixed_models}),
        )
        respx.get(BASE_URL).mock(return_value=httpx.Response(200))
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                400,
                json={"error": {"message": "model not found"}},
            ),
        )
        respx.post(EMBED_URL).mock(
            return_value=httpx.Response(
                400,
                json={"error": {"message": "model not found"}},
            ),
        )
        result = runner.invoke(cli, ["fcio", "capabilities"])
    assert result.exit_code == 0
    assert "Other Models" in result.output
    assert "whisper-large:1b" in result.output
    assert "created: 12345" in result.output


# ── models detail re-raise non-404 ApiError ───────────────


@respx.mock
def test_models_detail_server_error(runner: CliRunner, cli: click.Group) -> None:
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        respx.get(f"{API_BASE}/models/test-model").mock(
            return_value=httpx.Response(500, json={"detail": "Internal Server Error"}),
        )
        result = runner.invoke(cli, ["fcio", "models", "test-model"])
    assert result.exit_code != 0
    assert result.exception is not None


# ── _resolve_model fzf path ──────────────────────────────


@respx.mock
def test_resolve_model_fzf_pick(runner: CliRunner, cli: click.Group) -> None:
    """fzf binary exists and returns a model match."""
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("shutil.which", return_value="/usr/bin/fzf"),
        patch(
            "subprocess.run",
            return_value=MagicMock(stdout="gpt-oss:20b\n"),
        ),
    ):
        _setup_models_route()
        _setup_chat_route("fzf reply")
        result = runner.invoke(
            cli, ["fcio", "chat", "--no-stream", "--no-markdown", "-m", "oss", "hi"]
        )
    assert result.exit_code == 0
    assert "fzf reply" in result.output


@respx.mock
def test_resolve_model_fzf_empty_pick_fallback(runner: CliRunner, cli: click.Group) -> None:
    """fzf returns empty string → falls through to substring match."""
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("shutil.which", return_value="/usr/bin/fzf"),
        patch(
            "subprocess.run",
            return_value=MagicMock(stdout="\n"),
        ),
    ):
        _setup_models_route()
        _setup_chat_route("fallback reply")
        result = runner.invoke(
            cli,
            ["fcio", "chat", "--no-stream", "--no-markdown", "-m", "gpt-oss:20b", "hi"],
        )
    assert result.exit_code == 0
    assert "fallback reply" in result.output


# ── get_api_key missing key ────────────────────────────────


def test_fcio_missing_api_key(runner: CliRunner, cli: click.Group) -> None:
    """fcio subcommand fails when API key is not set."""
    with patch(
        "llm_fcio.get_api_key",
        side_effect=click.ClickException("API key not found. Set with: llm keys set fcio-rzob"),
    ):
        result = runner.invoke(cli, ["fcio", "models"])
    assert result.exit_code != 0
    assert "API key not found" in result.output


# ── StreamingRenderer edge cases via simulate ─────────────


@patch("llm_fcio.time.sleep")
def test_simulate_with_renderer(mock_sleep: MagicMock, runner: CliRunner, cli: click.Group) -> None:
    """Simulate with Rich renderer (not --raw) exercises StreamingRenderer.feed/flush."""
    result = runner.invoke(cli, ["fcio", "simulate", "--speed", "fast"])
    assert result.exit_code == 0
    assert len(result.output) > 0


# ── chat streaming with renderer via CLI ──────────────────


def test_chat_streaming_with_renderer(runner: CliRunner, cli: click.Group) -> None:
    """Streaming chat with markdown rendering enabled."""
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("llm_fcio._resolve_model", return_value="gpt-oss:20b"),
        patch("llm_fcio._iter_sse_content", return_value=iter(["Hello", " **world**"])),
    ):
        result = runner.invoke(cli, ["fcio", "chat", "Say hello"])
    assert result.exit_code == 0
    assert "Hello" in result.output


# ── chat streaming httpx error ──────────────────────────────


def test_chat_streaming_httpx_error(runner: CliRunner, cli: click.Group) -> None:
    """Streaming chat with httpx error wraps in ApiError."""
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("llm_fcio._resolve_model", return_value="gpt-oss:20b"),
        patch("llm_fcio._iter_sse_content", side_effect=httpx.ConnectError("Connection refused")),
    ):
        result = runner.invoke(cli, ["fcio", "chat", "test"])
    assert result.exit_code != 0
    assert result.exception is not None


# ── chat non-streaming as_json ──────────────────────────────


@respx.mock
def test_chat_non_stream_json_output(runner: CliRunner, cli: click.Group) -> None:
    """Non-streaming chat with --json flag outputs full response."""
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Hello!"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                },
            ),
        )
        result = runner.invoke(cli, ["fcio", "chat", "--no-stream", "--json", "test"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "choices" in data
    assert data["choices"][0]["message"]["content"] == "Hello!"


# ── cache migration path ──────────────────────────────────


def test_cache_migration_old_to_new(tmp_path: Path) -> None:
    """Old rzob_models.json is migrated to fcio_models_rzob.json."""
    old_file = tmp_path / "rzob_models.json"
    old_file.write_text(json.dumps([{"id": "gpt-oss:20b", "safe_id": "gpt-oss-20b"}]))
    with patch("llm_fcio.llm.user_dir", return_value=tmp_path):
        from llm_fcio import _cache_path

        path = _cache_path("rzob")
    assert path.name == "fcio_models_rzob.json"
    assert not old_file.exists()
    assert path.exists()


# ── Coverage gap tests ───────────────────────────────────


@respx.mock
def test_make_client_verbose_auth_masking() -> None:
    """Covers auth header masking in _on_request (line 126: value = 'Bearer sk-***...')."""
    from llm_fcio import _make_client

    respx.get("https://api.test.com/v1/test").mock(
        return_value=httpx.Response(200, json={"ok": True}),
    )
    client = _make_client(verbose=True, timeout=5.0)
    try:
        resp = client.get(
            "https://api.test.com/v1/test",
            headers={"Authorization": "Bearer sk-real-secret-key"},
        )
        assert resp.status_code == 200
    finally:
        client.close()


@respx.mock
def test_make_client_verbose_non_json_request_body() -> None:
    """Covers non-JSON request body fallback in _on_request (lines 135-139)."""
    from llm_fcio import _make_client

    respx.post("https://api.test.com/v1/test").mock(
        return_value=httpx.Response(200, json={"ok": True}),
    )
    client = _make_client(verbose=True, timeout=5.0)
    try:
        resp = client.post(
            "https://api.test.com/v1/test",
            content=b"this is plain text, not json",
        )
        assert resp.status_code == 200
    finally:
        client.close()


@respx.mock
def test_make_client_verbose_empty_response_body() -> None:
    """Covers empty response body early return in _on_response (lines 154-155).

    httpx event hooks can't access response.text via respx (ResponseNotRead),
    so we extract the hook from a verbose client and call it directly with a
    pre-loaded response.
    """
    from llm_fcio import _make_client

    client = _make_client(verbose=True, timeout=5.0)
    try:
        response_hooks = client._event_hooks["response"]
        response = httpx.Response(200, content=b"")
        for hook in response_hooks:
            hook(response)
    finally:
        client.close()


def test_make_client_verbose_non_json_response_body() -> None:
    """Covers non-JSON response body fallback in _on_response (lines 164-167).

    Direct hook invocation because respx doesn't set _content during hooks.
    """
    from llm_fcio import _make_client

    client = _make_client(verbose=True, timeout=5.0)
    try:
        response_hooks = client._event_hooks["response"]
        response = httpx.Response(
            200,
            content=b"plain text response body",
            headers={"content-type": "text/plain"},
        )
        for hook in response_hooks:
            hook(response)
    finally:
        client.close()


# ── Group B: get_api_key real path ───────────────────────


def test_get_api_key_success() -> None:
    """Covers get_api_key success path (lines 100-105)."""
    from llm_fcio import LOCATIONS, get_api_key

    with patch("llm_fcio.llm.get_key", return_value="my-api-key"):
        result = get_api_key(LOCATIONS["rzob"])
    assert result == "my-api-key"


def test_get_api_key_missing() -> None:
    """Covers get_api_key ClickException when key is None (lines 101-104)."""
    from llm_fcio import LOCATIONS, get_api_key

    with (
        patch("llm_fcio.llm.get_key", return_value=None),
        pytest.raises(click.ClickException, match="API key not found"),
    ):
        get_api_key(LOCATIONS["rzob"])


# ── Group D: _resolve_model fzf FileNotFoundError ────────


@respx.mock
def test_resolve_model_fzf_file_not_found(runner: CliRunner, cli: click.Group) -> None:
    """Covers FileNotFoundError in fzf subprocess (lines 285-286)."""
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("shutil.which", return_value="/usr/bin/fzf"),
        patch("subprocess.run", side_effect=FileNotFoundError("fzf binary vanished")),
    ):
        _setup_models_route()
        _setup_chat_route("fnf fallback reply")
        result = runner.invoke(
            cli,
            ["fcio", "chat", "--no-stream", "--no-markdown", "-m", "oss:20b", "hi"],
        )
    assert result.exit_code == 0
    assert "fnf fallback reply" in result.output


# ── Group E: Capabilities _probe_endpoint ────────────────


@respx.mock
def test_capabilities_probe_endpoint_success(runner: CliRunner, cli: click.Group) -> None:
    """Covers _probe_endpoint returning available on 200 response (line 953)."""
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(200, json={"choices": []}),
        )
        respx.post(EMBED_URL).mock(
            return_value=httpx.Response(200, json={"data": []}),
        )
        result = runner.invoke(cli, ["fcio", "capabilities"])
    assert result.exit_code == 0
    assert "\u2705 available" in result.output


@respx.mock
def test_capabilities_probe_non_auth_error(runner: CliRunner, cli: click.Group) -> None:
    """Covers _probe_endpoint error string for non-auth errors (line 959)."""
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(403, json={"detail": "Rate limited"}),
        )
        respx.post(EMBED_URL).mock(
            return_value=httpx.Response(403, json={"detail": "Rate limited"}),
        )
        result = runner.invoke(cli, ["fcio", "capabilities"])
    assert result.exit_code == 0
    assert "\u274c" in result.output
    assert "Rate limited" in result.output


# ── Group F: ingest confirmation + existing collection ──


@patch("llm_fcio.llm.Collection")
@patch("llm_fcio.llm.user_dir")
def test_ingest_with_confirmation_dialog(
    mock_user_dir: MagicMock,
    mock_collection_cls: MagicMock,
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    """Covers the confirmation dialog branch (lines 1270-1278)."""
    mock_user_dir.return_value = tmp_path
    mock_col = MagicMock()
    mock_col.model.return_value.model_id = "bge-m3-567m"
    mock_collection_cls.exists.return_value = False
    mock_collection_cls.return_value = mock_col

    doc = tmp_path / "test.md"
    doc.write_text("# Test\nHello world\n")

    with patch("click.confirm", return_value=True):
        result = runner.invoke(cli, ["fcio", "ingest", "testcol", str(doc)])
    assert result.exit_code == 0
    assert "Files to ingest" in result.output
    assert "Ingested" in result.output


@patch("llm_fcio.llm.Collection")
@patch("llm_fcio.llm.user_dir")
def test_ingest_existing_collection(
    mock_user_dir: MagicMock,
    mock_collection_cls: MagicMock,
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    """Covers existing collection path (line 1283: col = llm.Collection(collection, db))."""
    mock_user_dir.return_value = tmp_path
    mock_col = MagicMock()
    mock_col.model.return_value.model_id = "bge-m3-567m"
    mock_collection_cls.exists.return_value = True
    mock_collection_cls.return_value = mock_col

    doc = tmp_path / "test.md"
    doc.write_text("# Test\nHello\n")

    result = runner.invoke(cli, ["fcio", "ingest", "testcol", str(doc), "--yes"])
    assert result.exit_code == 0
    assert "Ingested" in result.output


@patch("llm_fcio.llm.Collection")
@patch("llm_fcio.llm.user_dir")
def test_ingest_tracked_generator_consumed(
    mock_user_dir: MagicMock,
    mock_collection_cls: MagicMock,
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    """Covers _tracked() generator body (lines 1303-1308) by consuming the iterator."""
    mock_user_dir.return_value = tmp_path
    mock_col = MagicMock()
    mock_col.model.return_value.model_id = "bge-m3-567m"
    mock_collection_cls.exists.return_value = False
    mock_collection_cls.return_value = mock_col

    consumed: list[tuple[str, str]] = []

    def consume_gen(gen, **_kwargs):  # noqa: ANN001, ANN003, ANN202
        for item in gen:
            consumed.append(item)

    mock_col.embed_multi.side_effect = consume_gen

    doc = tmp_path / "test.md"
    doc.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

    result = runner.invoke(cli, ["fcio", "ingest", "testcol", str(doc), "--yes"])
    assert result.exit_code == 0
    assert "Ingested" in result.output
    assert len(consumed) > 0


# ── Group G: _send_chat_request streaming paths ─────────


def test_chat_streaming_with_markdown_render(runner: CliRunner, cli: click.Group) -> None:
    """Covers streaming chat with renderer active (lines 1359, 1363: renderer.feed/flush)."""
    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("llm_fcio._resolve_model", return_value="gpt-oss:20b"),
        patch("llm_fcio._iter_sse_content", return_value=iter(["Hello", " **world**"])),
    ):
        result = runner.invoke(cli, ["fcio", "chat", "--markdown", "Say hello"])
    assert result.exit_code == 0
    assert "Hello" in result.output


def test_chat_streaming_api_error_reraise(runner: CliRunner, cli: click.Group) -> None:
    """Covers ApiError re-raise in streaming path (lines 1366-1367)."""
    from llm_fcio import ApiError

    with (
        patch("llm_fcio.get_api_key", return_value="test-key"),
        patch("llm_fcio._resolve_model", return_value="gpt-oss:20b"),
        patch("llm_fcio._iter_sse_content", side_effect=ApiError("stream error")),
    ):
        result = runner.invoke(cli, ["fcio", "chat", "--no-markdown", "test"])
    assert result.exit_code != 0


@respx.mock
def test_chat_non_stream_empty_choices(runner: CliRunner, cli: click.Group) -> None:
    """Covers empty choices raising ApiError in non-streaming path (line 1375)."""
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(200, json={"choices": []}),
        )
        result = runner.invoke(cli, ["fcio", "chat", "--no-stream", "test"])
    assert result.exit_code != 0
    assert result.exception is not None


# ── Additional coverage gap tests ───────────────────────


@respx.mock
def test_make_client_verbose_long_non_json_request() -> None:
    """Covers 500-char truncation of non-JSON request body (line 138)."""
    from llm_fcio import _make_client

    respx.post("https://api.test.com/v1/test").mock(
        return_value=httpx.Response(200, json={"ok": True}),
    )
    client = _make_client(verbose=True, timeout=5.0)
    try:
        long_body = b"x" * 600
        resp = client.post(
            "https://api.test.com/v1/test",
            content=long_body,
        )
        assert resp.status_code == 200
    finally:
        client.close()


def test_make_client_verbose_long_non_json_response() -> None:
    """Covers 500-char truncation of non-JSON response body (lines 165-166)."""
    from llm_fcio import _make_client

    client = _make_client(verbose=True, timeout=5.0)
    try:
        response_hooks = client._event_hooks["response"]
        response = httpx.Response(
            200,
            content=b"y" * 600,
            headers={"content-type": "text/plain"},
        )
        for hook in response_hooks:
            hook(response)
    finally:
        client.close()


def test_make_client_verbose_large_json_response() -> None:
    """Covers 2000-char truncation of JSON response body (line 160)."""
    from llm_fcio import _make_client

    client = _make_client(verbose=True, timeout=5.0)
    try:
        response_hooks = client._event_hooks["response"]
        large_data = {"key": "x" * 3000}
        response = httpx.Response(200, json=large_data)
        for hook in response_hooks:
            hook(response)
    finally:
        client.close()


@patch("llm_fcio.llm.Collection")
@patch("llm_fcio.llm.user_dir")
def test_ingest_confirmation_abort(
    mock_user_dir: MagicMock,
    mock_collection_cls: MagicMock,
    runner: CliRunner,
    cli: click.Group,
    tmp_path: Path,
) -> None:
    """Covers abort path in confirmation dialog (line 1278)."""
    mock_user_dir.return_value = tmp_path
    mock_col = MagicMock()
    mock_col.model.return_value.model_id = "bge-m3-567m"
    mock_collection_cls.exists.return_value = False
    mock_collection_cls.return_value = mock_col

    doc = tmp_path / "test.md"
    doc.write_text("# Test\nHello world\n")

    with patch("click.confirm", return_value=False):
        result = runner.invoke(cli, ["fcio", "ingest", "testcol", str(doc)])
    assert result.exit_code != 0
    assert "Aborted" in result.output


@respx.mock
def test_chat_with_max_tokens(runner: CliRunner, cli: click.Group) -> None:
    """Covers max_tokens body field in _build_chat_body (line 1335)."""
    with patch("llm_fcio.get_api_key", return_value="test-key"):
        _setup_models_route()
        _setup_chat_route("Limited reply")
        result = runner.invoke(
            cli,
            ["fcio", "chat", "--no-stream", "--no-markdown", "--max-tokens", "100", "test"],
        )
    assert result.exit_code == 0
    assert "Limited reply" in result.output
