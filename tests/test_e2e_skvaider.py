"""E2E tests for llm-fcio against skvaider with DummyBackend.

Starts skvaider as a real uvicorn process on a random port with an in-memory
DummyBackend — no real inference servers needed. Tests all public API functions
of llm_fcio.py against this real HTTP server.

The server runs in a daemon background thread and is cleaned up after the
test session. Each test gets an isolated user_dir (tmp_path) and patched
LOCATIONS dict pointing to the skvaider server.

Marked with @pytest.mark.e2e — run with: pytest -m e2e
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import TYPE_CHECKING

import llm
import pytest
import uvicorn

if TYPE_CHECKING:
    from fastapi import FastAPI

try:
    import svcs
    from skvaider import app_factory
    from skvaider.auth import AdminTokens
    from skvaider.config import (
        AuthConfig,
        Config,
        LoggingConfig,
        ModelInstanceConfig,
        ServerConfig,
        parse_size,
    )
    from skvaider.proxy.backends import DummyBackend
    from skvaider.proxy.models import AIModel
    from skvaider.proxy.pool import Pool
except ImportError:
    pytest.skip("skvaider not installed", allow_module_level=True)

import llm_fcio
from llm_fcio import LOCATIONS, Location

TEST_TOKEN = "test-admin-token"


# ── Test Lifespan ─────────────────────────────────────────────


@svcs.fastapi.lifespan
async def _test_lifespan(app: FastAPI, registry: svcs.Registry) -> AsyncGenerator[None]:
    """Set up a DummyBackend with test models in the skvaider pool."""
    backend = DummyBackend("http://dummy-backend")
    backend.healthy = True
    backend.map_up.mark("up")
    backend.memory = {
        "ram": {"free": 100 * 1024**3, "total": 100 * 1024**3},
    }

    for mid_id in ("gpt-oss:20b", "gpt-oss:120b"):
        m = AIModel(id=mid_id, owned_by="test", backend=backend)
        m.is_loaded = True
        m.memory_usage = {"ram": 1024**3}
        m.limit = 10
        backend.models[mid_id] = m

    pool = Pool(
        [
            ModelInstanceConfig(
                id="gpt-oss:20b",
                instances=1,
                memory={"ram": parse_size("1G")},
                task="chat",
            ),
            ModelInstanceConfig(
                id="gpt-oss:120b",
                instances=1,
                memory={"ram": parse_size("2G")},
                task="chat",
            ),
        ],
        [backend],
        data_dir=app.state.config.server.directory,
    )

    await pool.rebalance()

    registry.register_value(Pool, pool)
    registry.register_value(AdminTokens, AdminTokens([TEST_TOKEN]))

    yield

    pool.close()


# ── Server Fixture (Session scope) ────────────────────────────


@pytest.fixture(scope="session")
def skvaider_server_port(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[int]:
    """Start skvaider with DummyBackend on a random port, yield port, then shutdown.

    Uses the same uvicorn.Server + threading.Thread pattern as
    test_e2e_dummy_server.py.
    """
    tmp_path = tmp_path_factory.mktemp("skvaider")

    config = Config(
        auth=AuthConfig(admin_tokens=[TEST_TOKEN]),
        server=ServerConfig(directory=tmp_path),
        backend=[],
        models=[],
        logging=LoggingConfig(),
    )

    app = app_factory(config, lifespan=_test_lifespan)
    uvicorn_config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(uvicorn_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to start (with timeout)
    timeout = 5.0
    start = time.time()
    while not server.started:
        if time.time() - start > timeout:
            raise RuntimeError("Skvaider server failed to start within 5 seconds")
        time.sleep(0.01)

    port = server.servers[0].sockets[0].getsockname()[1]

    yield port

    # Clean shutdown
    server.should_exit = True
    thread.join(timeout=5.0)


# ── Per-Test Fixtures ──────────────────────────────────────────


@pytest.fixture()
def skvaider_api_base(skvaider_server_port: int) -> str:
    """API base URL pointing to the skvaider server."""
    return f"http://127.0.0.1:{skvaider_server_port}/openai/v1"


@pytest.fixture()
def patched_locations(skvaider_api_base: str, monkeypatch: pytest.MonkeyPatch) -> Location:
    """Monkey-patch LOCATIONS['rzob'] to point to the skvaider server.

    The plugin makes real HTTP requests to the uvicorn server — no ASGI transport
    patching needed.
    """
    loc = Location(
        name="rzob",
        api_base=skvaider_api_base,
        key_name="fcio-rzob",
        env_var="FCIO_RZOB_API_KEY",
    )
    monkeypatch.setitem(LOCATIONS, "rzob", loc)
    return loc


@pytest.fixture()
def patched_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch get_api_key to return the test admin token.

    Skvaider validates the Bearer token against AdminTokens, so we pass
    TEST_TOKEN which was registered in _test_lifespan.
    """
    monkeypatch.setattr("llm_fcio.get_api_key", lambda _loc: TEST_TOKEN)


@pytest.fixture()
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect llm.user_dir() to tmp_path for cache isolation."""
    monkeypatch.setattr(llm, "user_dir", lambda: tmp_path)
    return tmp_path


# ── Tests ─────────────────────────────────────────────────────


class TestSkvaiderE2E:
    """E2E tests exercising llm-fcio public API against a real skvaider app.

    Patching via LOCATIONS only — no _make_client monkeypatch. The plugin
    creates real httpx.Client instances making real HTTP requests to the
    uvicorn server.
    """

    @pytest.mark.e2e
    def test_list_models(self, patched_locations: Location, patched_api_key: None) -> None:
        """list_models returns models registered in DummyBackend."""
        models = llm_fcio.list_models()
        assert len(models) == 2
        ids = [m["id"] for m in models]
        assert "gpt-oss:20b" in ids
        assert "gpt-oss:120b" in ids

    @pytest.mark.e2e
    def test_list_models_with_filter(
        self, patched_locations: Location, patched_api_key: None
    ) -> None:
        """list_models with substring filter returns matching subset."""
        models = llm_fcio.list_models(filter="gpt-oss:120b")
        assert len(models) == 1
        assert models[0]["id"] == "gpt-oss:120b"

    @pytest.mark.e2e
    def test_get_model_info(self, patched_locations: Location, patched_api_key: None) -> None:
        """get_model_info returns details for a specific model."""
        info = llm_fcio.get_model_info("gpt-oss:20b")
        assert info["id"] == "gpt-oss:20b"
        assert info["owned_by"] == "test"

    @pytest.mark.e2e
    def test_get_model_info_unknown(
        self, patched_locations: Location, patched_api_key: None
    ) -> None:
        """get_model_info raises ModelError for unknown model."""
        with pytest.raises(llm_fcio.ModelError, match="Model not found"):
            llm_fcio.get_model_info("nonexistent-model")

    @pytest.mark.e2e
    def test_refresh_models(
        self, patched_locations: Location, patched_api_key: None, user_dir: Path
    ) -> None:
        """refresh_models fetches models from API and caches them."""
        models = llm_fcio.refresh_models()
        assert len(models) == 2
        ids = [m["id"] for m in models]
        assert "gpt-oss:20b" in ids

        # Cache file should now exist
        cache = llm_fcio._cache_path("rzob")
        assert cache.exists()
        cached = json.loads(cache.read_text())
        assert len(cached) == 2

    @pytest.mark.e2e
    def test_get_cached_models(
        self, patched_locations: Location, patched_api_key: None, user_dir: Path
    ) -> None:
        """get_cached_models reads from cache without calling API."""
        # First refresh to populate cache
        llm_fcio.refresh_models()

        # Now read from cache
        models = llm_fcio.get_cached_models()
        assert len(models) == 2
        assert models[0]["id"] == "gpt-oss:20b"

    @pytest.mark.e2e
    def test_get_cached_models_empty(
        self, patched_locations: Location, patched_api_key: None, user_dir: Path
    ) -> None:
        """get_cached_models returns [] when no cache exists."""
        assert llm_fcio.get_cached_models() == []

    @pytest.mark.e2e
    def test_get_capabilities(self, patched_locations: Location, patched_api_key: None) -> None:
        """get_capabilities probes endpoints and returns structured result."""
        result = llm_fcio.get_capabilities()

        # Endpoint info
        assert result["endpoint"]["name"] == "rzob"
        assert result["endpoint"]["auth"] == "valid"

        # Models should be present
        assert result["models"]["counts"]["total"] == 2
        assert result["models"]["counts"]["chat"] == 2
        assert result["models"]["counts"]["embedding"] == 0

        # Features should include probes for all endpoints
        features = result["features"]
        assert "chat_completions" in features
        assert "streaming" in features
        assert "embeddings" in features

        # Chat completions probe returns "available" even when model
        # does not exist (skvaider returns 400 with "model" in error,
        # which _probe_endpoint treats as "available")
        assert features["chat_completions"]["status"] == "available", (
            f"chat status: {features['chat_completions']['status']}"
        )
        assert features["streaming"]["status"] == "available", (
            f"stream status: {features['streaming']['status']}"
        )
        # Embeddings also returns "available" via the model error marker
        assert features["embeddings"]["status"] == "available", (
            f"embed status: {features['embeddings']['status']}"
        )

    @pytest.mark.e2e
    def test_estimate_tokens(self, patched_locations: Location, patched_api_key: None) -> None:
        """estimate_tokens returns token usage estimate."""
        result = llm_fcio.estimate_tokens("Hello world", model_id="gpt-oss:20b")
        # DummyBackend returns {"id": "cmpl-1", "choices": []} with no
        # usage data, so estimate_tokens returns the raw usage dict
        # (empty in this case since the API response lacks "usage").
        assert result == {}
