"""End-to-end tests using a real dummy FastAPI server.

Starts a minimal FastAPI server on a random port implementing OpenAI-compatible
API endpoints (GET /v1/models, GET /v1/models/{id}, POST /v1/chat/completions,
POST /v1/embeddings). Tests all public API functions of llm_fcio.py against
this real HTTP server.

The server runs in a daemon background thread and is cleaned up after the
test session. Each test gets an isolated user_dir (tmp_path) and patched
LOCATIONS dict pointing to the test server.

Marked with @pytest.mark.e2e — run with: pytest -m e2e
"""

import json
import sys
import threading
import time
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import llm
import pytest
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from llm_fcio import LOCATIONS, Location

# ── Sample Model Data ──────────────────────────────────────────

SAMPLE_MODELS = [
    {"id": "gpt-oss:20b", "owned_by": "fcio", "created": 1700000000},
    {"id": "gpt-oss:120b", "owned_by": "fcio", "created": 1700000001},
    {"id": "bge-m3:567m", "owned_by": "fcio", "created": 1700000002},
    {"id": "Nomic-embed-text:v1.5", "owned_by": "fcio", "created": 1700000003},
]

TEST_KEY = "test-sk-e2e-dummy-key"


# ── Dummy Server ───────────────────────────────────────────────


def create_app() -> FastAPI:
    """Create the dummy OpenAI-compatible API server."""

    app = FastAPI()

    @app.get("/v1/models")
    async def list_models():
        return {"data": SAMPLE_MODELS, "object": "list"}

    @app.get("/v1/models/{model_id}")
    async def get_model(model_id: str):
        for m in SAMPLE_MODELS:
            if m["id"] == model_id:
                return {"data": m}
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    async def _sse_chunks(content: str, model_id: str) -> AsyncGenerator[str, None]:
        """Yield SSE events for streaming chat completion."""
        chunk_size = 8
        for i in range(0, max(1, len(content)), chunk_size):
            text_chunk = content[i : i + chunk_size]
            event = {
                "id": "chatcmpl-e2e-stream",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": text_chunk},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(event)}\n\n"

        final = {
            "id": "chatcmpl-e2e-stream",
            "object": "chat.completion.chunk",
            "model": model_id,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"

    @app.post("/v1/chat/completions")
    async def chat_completions(body: dict):
        """Return realistic OpenAI chat completion response.

        Supports both streaming (SSE) and non-streaming modes.
        Probe requests (model contains '_probe_test') return 400 with
        'model not found' — matching what get_capabilities expects.
        """
        if "_probe_test" in body.get("model", ""):
            raise HTTPException(
                status_code=400,
                detail={"error": {"message": "model not found"}},
            )

        content = ""
        for msg in body.get("messages", []):
            c = msg.get("content", "")
            if isinstance(c, str):
                content += c

        prompt_tokens = max(1, len(content) // 4)
        response_content = f"Echo: {content[:200]}"
        model_id = body.get("model", "unknown")

        if body.get("stream"):
            return StreamingResponse(
                _sse_chunks(response_content, model_id),
                media_type="text/event-stream",
            )

        return {
            "id": "chatcmpl-e2e-123",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 10,
                "total_tokens": prompt_tokens + 10,
            },
        }

    @app.post("/v1/embeddings")
    async def embeddings(body: dict):
        """Return realistic OpenAI embedding response."""
        if "_probe_test" in body.get("model", ""):
            raise HTTPException(
                status_code=400,
                detail={"error": {"message": "model not found"}},
            )

        inputs = body.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]

        data = []
        for i, _ in enumerate(inputs):
            data.append(
                {
                    "object": "embedding",
                    "index": i,
                    "embedding": [0.1] * 384,
                }
            )

        return {
            "object": "list",
            "data": data,
            "model": body.get("model", "unknown"),
            "usage": {"prompt_tokens": 10, "total_tokens": 10},
        }

    return app


# ── Server Fixture (Session scope) ────────────────────────────


@pytest.fixture(scope="session")
def dummy_server_port() -> Generator[int, None, None]:
    """Start the dummy server on a random port, yield port, then shutdown."""
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to start (with timeout)
    timeout = 5.0
    start = time.time()
    while not server.started:
        if time.time() - start > timeout:
            raise RuntimeError("Dummy server failed to start within 5 seconds")
        time.sleep(0.01)

    port = server.servers[0].sockets[0].getsockname()[1]

    yield port

    # Clean shutdown
    server.should_exit = True
    thread.join(timeout=5.0)


# ── Per-Test Fixtures ──────────────────────────────────────────


@pytest.fixture()
def test_api_base(dummy_server_port: int) -> str:
    """API base URL pointing to the dummy server."""
    return f"http://127.0.0.1:{dummy_server_port}/v1"


@pytest.fixture()
def patch_location(test_api_base: str, monkeypatch: pytest.MonkeyPatch) -> Location:
    """Monkey-patch LOCATIONS['rzob'] to point to the test server."""
    loc = Location(
        name="rzob",
        api_base=test_api_base,
        key_name="fcio-rzob",
        env_var="FCIO_RZOB_API_KEY",
    )
    monkeypatch.setitem(LOCATIONS, "rzob", loc)
    return loc


@pytest.fixture()
def patch_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch get_api_key to return the test key (server doesn't validate)."""
    monkeypatch.setattr("llm_fcio.get_api_key", lambda _loc: TEST_KEY)


@pytest.fixture()
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect llm.user_dir() to tmp_path for cache isolation."""
    monkeypatch.setattr(llm, "user_dir", lambda: tmp_path)
    return tmp_path


# ── Tests ──────────────────────────────────────────────────────


class TestE2E:
    """E2E tests using a real dummy server.

    Tests exercise the full HTTP round-trip through a real FastAPI/uvicorn
    server. Only the LOCATIONS dict and get_api_key are patched to point
    at the test server instead of the real FCIO API.
    """

    # ── list_models ─────────────────────────────────────────────

    @pytest.mark.e2e
    def test_list_models_all(self, patch_location: Location, patch_api_key: None) -> None:
        """list_models() returns all models from the dummy server."""
        from llm_fcio import list_models

        models = list_models("rzob")
        assert len(models) == 4
        assert models[0]["id"] == "gpt-oss:20b"
        assert models[1]["id"] == "gpt-oss:120b"
        assert models[2]["id"] == "bge-m3:567m"

    @pytest.mark.e2e
    def test_list_models_with_filter(self, patch_location: Location, patch_api_key: None) -> None:
        """list_models(filter='bge') returns only matching models."""
        from llm_fcio import list_models

        models = list_models("rzob", filter="bge")
        assert len(models) == 1
        assert models[0]["id"] == "bge-m3:567m"

    @pytest.mark.e2e
    def test_list_models_empty_filter(self, patch_location: Location, patch_api_key: None) -> None:
        """list_models(filter='nonexistent') returns empty list."""
        from llm_fcio import list_models

        models = list_models("rzob", filter="zzz-nonexistent")
        assert models == []

    # ── get_model_info ─────────────────────────────────────────

    @pytest.mark.e2e
    def test_get_model_info_found(self, patch_location: Location, patch_api_key: None) -> None:
        """get_model_info() returns details for a known model."""
        from llm_fcio import get_model_info

        info = get_model_info("gpt-oss:20b", "rzob")
        assert info["id"] == "gpt-oss:20b"
        assert info["owned_by"] == "fcio"
        assert info["created"] == 1700000000

    @pytest.mark.e2e
    def test_get_model_info_not_found(self, patch_location: Location, patch_api_key: None) -> None:
        """get_model_info() raises ModelError for unknown model."""
        from llm_fcio import ModelError, get_model_info

        with pytest.raises(ModelError, match="Model not found"):
            get_model_info("nonexistent-model", "rzob")

    # ── refresh_models ─────────────────────────────────────────

    @pytest.mark.e2e
    def test_refresh_models_creates_cache(
        self,
        patch_location: Location,
        patch_api_key: None,
        user_dir: Path,
    ) -> None:
        """refresh_models() fetches models from dummy server and writes cache."""
        from llm_fcio import refresh_models

        models = refresh_models("rzob")
        assert len(models) == 4

        cache_file = user_dir / "fcio_models_rzob.json"
        assert cache_file.exists()

        cached = json.loads(cache_file.read_text())
        assert len(cached) == 4
        assert cached[0]["id"] == "gpt-oss:20b"
        # Verify safe_id was generated
        assert cached[0]["safe_id"] == "gpt-oss-20b"

    @pytest.mark.e2e
    def test_refresh_models_empty_list(
        self,
        patch_location: Location,
        patch_api_key: None,
        user_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """refresh_models() handles empty model list."""
        # Override the dummy server endpoint for this test
        cache_file = user_dir / "fcio_models_rzob.json"

        # We need to intercept the request to return empty
        with patch("llm_fcio.api_request") as mock_request:
            mock_request.return_value = httpx.Response(200, json={"data": []})
            from llm_fcio import refresh_models

            models = refresh_models("rzob")
            assert models == []
            assert cache_file.exists()
            assert json.loads(cache_file.read_text()) == []

    # ── get_cached_models ──────────────────────────────────────

    @pytest.mark.e2e
    def test_get_cached_models_after_refresh(
        self,
        patch_location: Location,
        patch_api_key: None,
        user_dir: Path,
    ) -> None:
        """get_cached_models() returns cached models without API call."""
        from llm_fcio import get_cached_models, refresh_models

        # Populate cache via API
        refresh_models("rzob")

        # Read back without API — should use cache
        cached = get_cached_models("rzob")
        assert len(cached) == 4
        assert cached[0]["id"] == "gpt-oss:20b"

    @pytest.mark.e2e
    def test_get_cached_models_empty(
        self, patch_location: Location, patch_api_key: None, user_dir: Path
    ) -> None:
        """get_cached_models() returns empty list when cache doesn't exist."""
        from llm_fcio import get_cached_models

        cached = get_cached_models("rzob")
        assert cached == []

    # ── get_capabilities ───────────────────────────────────────

    @pytest.mark.e2e
    def test_get_capabilities_structure(
        self,
        patch_location: Location,
        patch_api_key: None,
    ) -> None:
        """get_capabilities() probes endpoints and returns structured result."""
        from llm_fcio import get_capabilities

        result = get_capabilities("rzob")

        # Endpoint info
        assert result["endpoint"]["name"] == "rzob"
        assert "api_base" in result["endpoint"]
        assert result["endpoint"]["auth"] == "valid"

        # Model counts
        assert result["models"]["counts"]["total"] == 4
        assert result["models"]["counts"]["chat"] >= 2
        assert result["models"]["counts"]["embedding"] >= 1

        # Feature probes — all should be "available" since our dummy
        # server returns 400 with "model not found" for probe requests,
        # which _probe_endpoint treats as "available"
        assert result["features"]["chat_completions"]["status"] == "available"
        assert result["features"]["streaming"]["status"] == "available"
        assert result["features"]["embeddings"]["status"] == "available"

    @pytest.mark.e2e
    def test_get_capabilities_model_categorization(
        self,
        patch_location: Location,
        patch_api_key: None,
    ) -> None:
        """get_capabilities() correctly categorizes models by type."""
        from llm_fcio import get_capabilities

        result = get_capabilities("rzob")

        # gpt-oss models should be in chat
        chat_ids = [m["id"] for m in result["models"]["chat"]]
        assert "gpt-oss:20b" in chat_ids
        assert "gpt-oss:120b" in chat_ids

        # bge-m3 and Nomic-embed should be in embedding
        embed_ids = [m["id"] for m in result["models"]["embedding"]]
        assert "bge-m3:567m" in embed_ids
        assert "Nomic-embed-text:v1.5" in embed_ids

    # ── estimate_tokens ────────────────────────────────────────

    @pytest.mark.e2e
    def test_estimate_tokens_returns_usage(
        self,
        patch_location: Location,
        patch_api_key: None,
    ) -> None:
        """estimate_tokens() returns usage info from the dummy server."""
        from llm_fcio import estimate_tokens

        result = estimate_tokens(
            "Hello world, this is a test message for token estimation.",
            "gpt-oss:20b",
            "rzob",
        )
        assert "prompt_tokens" in result
        assert result["prompt_tokens"] > 0
        assert "completion_tokens" in result
        assert result["completion_tokens"] == 10
        assert "total_tokens" in result
        assert "_fallback" not in result

    @pytest.mark.e2e
    def test_estimate_tokens_fallback_on_error(
        self,
        patch_location: Location,
        patch_api_key: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """estimate_tokens() falls back to heuristic when API errors."""
        from llm_fcio import estimate_tokens

        # Patch api_request to raise ApiError
        with patch("llm_fcio.api_request") as mock_request:
            from llm_fcio import ApiError

            mock_request.side_effect = ApiError("500: Internal error", status_code=500)

            result = estimate_tokens("test", "gpt-oss:20b", "rzob")
            assert "_fallback" in result
            assert result["_fallback"] is True
            assert "prompt_tokens" in result

    # ── ingest_files ───────────────────────────────────────────

    @pytest.mark.e2e
    def test_ingest_files_single_file(
        self,
        patch_location: Location,
        patch_api_key: None,
        user_dir: Path,
        tmp_path: Path,
    ) -> None:
        """ingest_files() chunks a single file and calls embed_multi."""
        from llm_fcio import ingest_files

        doc = tmp_path / "test.md"
        doc.write_text("# Test\nHello world\nThis is a test file.\n")

        with (
            patch("llm_fcio.sqlite_utils.Database") as mock_db,
            patch("llm_fcio.llm.Collection") as mock_collection_cls,
        ):
            mock_col = MagicMock()
            mock_col.model.return_value.model_id = "bge-m3-567m"
            mock_collection_cls.exists.return_value = False
            mock_collection_cls.return_value = mock_col

            total = ingest_files(
                "testcol",
                str(doc),
                glob="*.md",
                model_id="bge-m3-567m",
                loc_name="rzob",
            )
            assert total == 1
            mock_col.embed_multi.assert_called_once()

    @pytest.mark.e2e
    def test_ingest_files_no_matching_files(
        self,
        patch_location: Location,
        patch_api_key: None,
        user_dir: Path,
        tmp_path: Path,
    ) -> None:
        """ingest_files() returns 0 when directory has no matching files."""
        from llm_fcio import ingest_files

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "readme.txt").write_text("Not markdown")

        total = ingest_files(
            "testcol",
            str(data_dir),
            glob="*.md",
            model_id="bge-m3-567m",
            loc_name="rzob",
        )
        assert total == 0

    # ── Full Workflow ─────────────────────────────────────────

    @pytest.mark.e2e
    def test_full_lifecycle(
        self,
        patch_location: Location,
        patch_api_key: None,
        user_dir: Path,
    ) -> None:
        """Complete lifecycle: list → refresh → cached → info."""
        from llm_fcio import get_cached_models, get_model_info, list_models, refresh_models

        # 1. List models from API
        models = list_models("rzob")
        assert len(models) == 4

        # 2. Get info for a specific model
        info = get_model_info("gpt-oss:20b", "rzob")
        assert info["id"] == "gpt-oss:20b"

        # 3. Refresh to populate cache
        refresh_models("rzob")

        # 4. Read from cache (no API call)
        cached = get_cached_models("rzob")
        assert len(cached) == 4
        assert cached[0]["id"] == "gpt-oss:20b"

    # ── Server Health Checks ──────────────────────────────────

    @pytest.mark.e2e
    def test_dummy_server_models_endpoint(self, test_api_base: str) -> None:
        """Verify the dummy server's /v1/models endpoint works directly."""
        resp = httpx.get(f"{test_api_base}/models", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 4

    @pytest.mark.e2e
    def test_dummy_server_chat_endpoint(self, test_api_base: str) -> None:
        """Verify the dummy server's /v1/chat/completions endpoint."""
        resp = httpx.post(
            f"{test_api_base}/chat/completions",
            json={
                "model": "gpt-oss:20b",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "choices" in data
        assert data["choices"][0]["message"]["content"].startswith("Echo:")

    @pytest.mark.e2e
    def test_dummy_server_embed_endpoint(self, test_api_base: str) -> None:
        """Verify the dummy server's /v1/embeddings endpoint."""
        resp = httpx.post(
            f"{test_api_base}/embeddings",
            json={"model": "bge-m3:567m", "input": ["test"]},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert len(data["data"][0]["embedding"]) == 384

    @pytest.mark.e2e
    def test_dummy_server_chat_streaming_endpoint(self, test_api_base: str) -> None:
        """Verify the dummy server's SSE streaming endpoint works."""
        resp = httpx.post(
            f"{test_api_base}/chat/completions",
            json={
                "model": "gpt-oss:20b",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
            timeout=5,
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        # Collect SSE events
        chunks = []
        for line in resp.text.split("\n"):
            if line.startswith("data: ") and line != "data: [DONE]":
                event = json.loads(line[6:])
                delta = event.get("choices", [{}])[0].get("delta", {})
                if delta.get("content"):
                    chunks.append(delta["content"])
        full = "".join(chunks)
        assert full.startswith("Echo:")


# ── Streaming Renderer E2E Tests ──────────────────────────────


class TestStreamingRendererE2E:
    """E2E tests for install_renderer_patch streaming output.

    Verifies that the patched llm.Response.__iter__ produces
    exactly one copy of output on stdout (via Rich renderer),
    not two (renderer + raw yield).

    Uses the session-scoped dummy server with SSE streaming support.
    """

    @pytest.fixture()
    def with_renderer(self, monkeypatch):
        """Install the streaming renderer patch for testing."""
        import llm
        import llm_fcio

        saved = llm.Response.__iter__
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        llm_fcio.install_renderer_patch()
        yield
        llm.Response.__iter__ = saved

    @pytest.fixture()
    def env_key(self, monkeypatch):
        """Set API key via environment variable for KeyModel.get_key()."""
        monkeypatch.setenv("FCIO_RZOB_API_KEY", TEST_KEY)

    @pytest.mark.e2e
    def test_streaming_single_output(
        self, patch_location, env_key, user_dir, with_renderer, capsys
    ) -> None:
        """Streaming through patched __iter__ outputs text exactly once.

        Simulates llm CLI behavior: iterate response and print each chunk.
        The patched __iter__ must suppress yields when the renderer is active,
        otherwise stdout gets both rendered output AND raw text (double output).
        """
        from llm_fcio import LOCATIONS, RzobModel

        loc = LOCATIONS["rzob"]
        model = RzobModel("fcio-rzob/test-stream", "gpt-oss:20b", loc)
        response = model.prompt("Hello E2E", stream=True)

        # Simulate llm CLI: iterate and print each yielded chunk
        for chunk in response:
            sys.stdout.write(chunk)
        sys.stdout.flush()

        captured = capsys.readouterr()

        # "Echo" must appear exactly ONCE — not twice (double output bug)
        echo_count = captured.out.count("Echo")
        assert echo_count == 1, (
            f"Expected 'Echo' exactly once, got {echo_count} times:\n{captured.out}"
        )

    @pytest.mark.e2e
    def test_non_streaming_single_output(
        self, patch_location, env_key, user_dir, with_renderer, capsys
    ) -> None:
        """Non-streaming response through patched __iter__ outputs text exactly once."""
        from llm_fcio import LOCATIONS, RzobModel

        loc = LOCATIONS["rzob"]
        model = RzobModel("fcio-rzob/test-nostream", "gpt-oss:20b", loc)
        response = model.prompt("Test non-streaming", stream=False)

        # Simulate llm CLI: iterate and print each yielded chunk
        for chunk in response:
            sys.stdout.write(chunk)
        sys.stdout.flush()

        captured = capsys.readouterr()

        echo_count = captured.out.count("Echo")
        assert echo_count == 1, (
            f"Expected 'Echo' exactly once, got {echo_count} times:\n{captured.out}"
        )

    @pytest.mark.e2e
    def test_renderer_fallback_yields_chunks(
        self, patch_location, env_key, user_dir, monkeypatch, capsys
    ) -> None:
        """When renderer.feed() fails, chunks are yielded as fallback."""
        import llm
        import llm_fcio
        from llm_fcio import LOCATIONS, RzobModel

        saved = llm.Response.__iter__
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setattr(
            llm_fcio._StreamingRenderer,
            "feed",
            lambda self, chunk: (_ for _ in ()).throw(RuntimeError("test fail")),
        )
        llm_fcio.install_renderer_patch()

        try:
            loc = LOCATIONS["rzob"]
            model = RzobModel("fcio-rzob/test-fallback", "gpt-oss:20b", loc)
            response = model.prompt("Fallback test", stream=True)

            collected = []
            for chunk in response:
                collected.append(chunk)
                sys.stdout.write(chunk)
            sys.stdout.flush()

            captured = capsys.readouterr()

            # Fallback: chunks should be yielded (not suppressed by renderer)
            assert len(collected) > 0, "Fallback must yield chunks"
            full_text = "".join(collected)
            assert "Echo" in full_text
            # Raw text appears once via sys.stdout.write
            assert captured.out.count("Echo") == 1
        finally:
            llm.Response.__iter__ = saved
