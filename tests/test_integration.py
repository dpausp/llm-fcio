"""Integration tests for HTTP-dependent functions in llm_fcio.

Uses respx to mock httpx requests — exercises real production code paths.
Only the HTTP layer is mocked; all internal code runs for real.
"""

import json
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import llm
import pytest
import respx

from llm_fcio import (
    LOCATIONS,
    ApiError,
    Location,
    ModelError,
    RzobEmbeddingModel,
    RzobModel,
    _iter_sse_content,
    _load_models,
    _resolve_model,
    api_request,
    register_embedding_models,
    register_models,
)

if TYPE_CHECKING:
    from respx.router import MockRouter

API_BASE = "https://ai.rzob.fcio.net/openai/v1"
TEST_KEY = "test-api-key-12345"


# ── Helpers ───────────────────────────────────────────────────


def _sse_event(data: str) -> str:
    """Format a single SSE data event."""
    return f"data: {data}\n\n"


def _sse_stream(events: list[str]) -> bytes:
    """Build an SSE byte stream from event data strings."""
    return "".join(_sse_event(e) for e in events).encode()


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture()
def mocked_api() -> Generator[MockRouter]:
    """Provide a respx mock router for HTTP interception."""
    with respx.mock as mock:
        yield mock


@pytest.fixture()
def api_key() -> str:
    """Test API key."""
    return TEST_KEY


@pytest.fixture()
def api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set FCIO_RZOB_API_KEY environment variable."""
    monkeypatch.setenv("FCIO_RZOB_API_KEY", TEST_KEY)


@pytest.fixture()
def rzob_loc() -> Location:
    """RZOB location object."""
    return LOCATIONS["rzob"]


@pytest.fixture()
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect llm.user_dir() to tmp_path for cache isolation."""
    monkeypatch.setattr(llm, "user_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def no_fzf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable fzf by making shutil.which return None."""
    monkeypatch.setattr("shutil.which", lambda _: None)


@pytest.fixture()
def cached_models(user_dir: Path) -> list[dict]:
    """Write a test model cache to the redirected user_dir."""
    models = [
        {"id": "gpt-oss:20b", "safe_id": "gpt-oss-20b"},
        {"id": "gpt-oss:120b", "safe_id": "gpt-oss-120b"},
        {"id": "bge-m3:567m", "safe_id": "bge-m3-567m"},
        {"id": "Nomic-embed-text:v1.5", "safe_id": "Nomic-embed-text-v1.5"},
    ]
    cache_file = user_dir / "fcio_models_rzob.json"
    cache_file.write_text(json.dumps(models))
    return models


@pytest.fixture()
def rzob_model(rzob_loc: Location) -> RzobModel:
    """Create a RzobModel instance for testing."""
    return RzobModel("fcio-rzob/gpt-oss-20b", "gpt-oss:20b", rzob_loc)


@pytest.fixture()
def embed_model(rzob_loc: Location) -> RzobEmbeddingModel:
    """Create a RzobEmbeddingModel instance for testing."""
    return RzobEmbeddingModel("fcio-rzob/bge-m3-567m", "bge-m3:567m", rzob_loc)


@pytest.fixture()
def simple_prompt(rzob_model: RzobModel) -> llm.Prompt:
    """Create a minimal Prompt for execute tests."""
    return llm.Prompt(
        "Say hello",
        model=rzob_model,
        options=RzobModel.Options(),
    )


@pytest.fixture()
def simple_response(simple_prompt: llm.Prompt, rzob_model: RzobModel) -> llm.Response:
    """Create a minimal Response for execute tests."""
    return llm.Response(model=rzob_model, prompt=simple_prompt, stream=False)


# ── api_request ───────────────────────────────────────────────


def test_api_request_get_success(mocked_api: MockRouter, api_key: str) -> None:
    """Successful GET returns response with JSON body."""
    mocked_api.get(f"{API_BASE}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-oss:20b"}]})
    )
    resp = api_request("GET", "/models", api_key, API_BASE)
    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == "gpt-oss:20b"


def test_api_request_sends_bearer_auth(mocked_api: MockRouter, api_key: str) -> None:
    """Request includes Authorization: Bearer header."""
    route = mocked_api.get(f"{API_BASE}/models").mock(return_value=httpx.Response(200, json={}))
    api_request("GET", "/models", api_key, API_BASE)
    request = route.calls[0].request
    assert request.headers["Authorization"] == f"Bearer {api_key}"


def test_api_request_post_sends_json_body(mocked_api: MockRouter, api_key: str) -> None:
    """POST request forwards the JSON body."""
    route = mocked_api.post(f"{API_BASE}/embeddings").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    body = {"model": "bge-m3", "input": ["hello"]}
    api_request("POST", "/embeddings", api_key, API_BASE, json_data=body)
    assert json.loads(route.calls[0].request.content) == body


def test_api_request_401_raises_api_error(mocked_api: MockRouter, api_key: str) -> None:
    """401 response raises ApiError with status code."""
    mocked_api.get(f"{API_BASE}/models").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid API key"})
    )
    with pytest.raises(ApiError) as exc_info:
        api_request("GET", "/models", api_key, API_BASE)
    assert exc_info.value.status_code == 401
    assert "401" in str(exc_info.value)


def test_api_request_nested_error_message(mocked_api: MockRouter, api_key: str) -> None:
    """Extracts message from nested error.message structure."""
    mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "model not found"}})
    )
    with pytest.raises(ApiError, match="model not found"):
        api_request("POST", "/chat/completions", api_key, API_BASE, json_data={})


def test_api_request_path_without_leading_slash(mocked_api: MockRouter, api_key: str) -> None:
    """Path without leading slash produces correct URL."""
    route = mocked_api.get(f"{API_BASE}/models").mock(return_value=httpx.Response(200, json={}))
    api_request("GET", "models", api_key, API_BASE)
    assert route.called


def test_api_request_forwards_query_params(mocked_api: MockRouter, api_key: str) -> None:
    """Query parameters appear in the request URL."""
    route = mocked_api.get(f"{API_BASE}/models").mock(return_value=httpx.Response(200, json={}))
    api_request("GET", "/models", api_key, API_BASE, params={"limit": 10})
    assert "limit=10" in str(route.calls[0].request.url)


def test_api_request_500_raises_api_error(mocked_api: MockRouter, api_key: str) -> None:
    """500 response raises ApiError."""
    mocked_api.get(f"{API_BASE}/models").mock(
        return_value=httpx.Response(500, json={"detail": "Internal Server Error"})
    )
    with pytest.raises(ApiError) as exc_info:
        api_request("GET", "/models", api_key, API_BASE)
    assert exc_info.value.status_code == 500


def test_api_request_content_type_json(mocked_api: MockRouter, api_key: str) -> None:
    """Request includes Content-Type: application/json."""
    route = mocked_api.get(f"{API_BASE}/models").mock(return_value=httpx.Response(200, json={}))
    api_request("GET", "/models", api_key, API_BASE)
    assert route.calls[0].request.headers["Content-Type"] == "application/json"


# ── _iter_sse_content ─────────────────────────────────────────


def test_iter_sse_yields_content_deltas(api_key: str) -> None:
    """SSE stream yields content deltas in order."""
    payload = _sse_stream(
        [
            '{"choices":[{"delta":{"content":"Hello"}}]}',
            '{"choices":[{"delta":{"content":" world"}}]}',
            "[DONE]",
        ]
    )
    with respx.mock:
        respx.post(f"{API_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=payload,
                headers={"Content-Type": "text/event-stream"},
            )
        )
        with httpx.Client() as client:
            meta, content_iter = _iter_sse_content(
                client,
                f"{API_BASE}/chat/completions",
                {"Authorization": f"Bearer {api_key}"},
                {"model": "test", "messages": [], "stream": True},
            )
            chunks = list(content_iter)
    assert chunks == ["Hello", " world"]


def test_iter_sse_done_sentinel_skipped(api_key: str) -> None:
    """[DONE] sentinel produces no output."""
    payload = _sse_stream(
        [
            '{"choices":[{"delta":{"content":"Hi"}}]}',
            "[DONE]",
        ]
    )
    with respx.mock:
        respx.post(f"{API_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=payload,
                headers={"Content-Type": "text/event-stream"},
            )
        )
        with httpx.Client() as client:
            meta, content_iter = _iter_sse_content(
                client,
                f"{API_BASE}/chat/completions",
                {"Authorization": f"Bearer {api_key}"},
                {"model": "test", "messages": [], "stream": True},
            )
            chunks = list(content_iter)
    assert chunks == ["Hi"]


def test_iter_sse_empty_choices_skipped(api_key: str) -> None:
    """Events with empty choices list are skipped."""
    payload = _sse_stream(
        [
            '{"choices":[]}',
            '{"choices":[{"delta":{"content":"data"}}]}',
            "[DONE]",
        ]
    )
    with respx.mock:
        respx.post(f"{API_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=payload,
                headers={"Content-Type": "text/event-stream"},
            )
        )
        with httpx.Client() as client:
            meta, content_iter = _iter_sse_content(
                client,
                f"{API_BASE}/chat/completions",
                {"Authorization": f"Bearer {api_key}"},
                {"model": "test", "messages": [], "stream": True},
            )
            chunks = list(content_iter)
    assert chunks == ["data"]


def test_iter_sse_malformed_json_skipped(api_key: str) -> None:
    """Malformed JSON in SSE event is silently skipped."""
    payload = _sse_stream(
        [
            "{bad json}",
            '{"choices":[{"delta":{"content":"ok"}}]}',
            "[DONE]",
        ]
    )
    with respx.mock:
        respx.post(f"{API_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=payload,
                headers={"Content-Type": "text/event-stream"},
            )
        )
        with httpx.Client() as client:
            meta, content_iter = _iter_sse_content(
                client,
                f"{API_BASE}/chat/completions",
                {"Authorization": f"Bearer {api_key}"},
                {"model": "test", "messages": [], "stream": True},
            )
            chunks = list(content_iter)
    assert chunks == ["ok"]


def test_iter_sse_delta_without_content_skipped(api_key: str) -> None:
    """Delta with no content field is skipped."""
    payload = _sse_stream(
        [
            '{"choices":[{"delta":{"role":"assistant"}}]}',
            '{"choices":[{"delta":{"content":"yes"}}]}',
            "[DONE]",
        ]
    )
    with respx.mock:
        respx.post(f"{API_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=payload,
                headers={"Content-Type": "text/event-stream"},
            )
        )
        with httpx.Client() as client:
            meta, content_iter = _iter_sse_content(
                client,
                f"{API_BASE}/chat/completions",
                {"Authorization": f"Bearer {api_key}"},
                {"model": "test", "messages": [], "stream": True},
            )
            chunks = list(content_iter)
    assert chunks == ["yes"]


def test_iter_sse_empty_delta_content_skipped(api_key: str) -> None:
    """Delta with empty-string content is skipped (falsy check)."""
    payload = _sse_stream(
        [
            '{"choices":[{"delta":{"content":""}}]}',
            '{"choices":[{"delta":{"content":"real"}}]}',
            "[DONE]",
        ]
    )
    with respx.mock:
        respx.post(f"{API_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=payload,
                headers={"Content-Type": "text/event-stream"},
            )
        )
        with httpx.Client() as client:
            meta, content_iter = _iter_sse_content(
                client,
                f"{API_BASE}/chat/completions",
                {"Authorization": f"Bearer {api_key}"},
                {"model": "test", "messages": [], "stream": True},
            )
            chunks = list(content_iter)
    assert chunks == ["real"]


# ── _resolve_model ────────────────────────────────────────────


def test_resolve_model_exact_match(mocked_api: MockRouter, api_key: str, no_fzf: None) -> None:
    """Exact model ID is returned without needing fuzzy matching."""
    mocked_api.get(f"{API_BASE}/models").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "gpt-oss:20b"}, {"id": "gpt-oss:120b"}]}
        )
    )
    result = _resolve_model("gpt-oss:20b", api_key, API_BASE)
    assert result == "gpt-oss:20b"


def test_resolve_model_substring_match(mocked_api: MockRouter, api_key: str, no_fzf: None) -> None:
    """Unique substring match resolves to the correct model."""
    mocked_api.get(f"{API_BASE}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-oss:20b"}]})
    )
    result = _resolve_model("oss", api_key, API_BASE)
    assert result == "gpt-oss:20b"


def test_resolve_model_ambiguous_raises_model_error(
    mocked_api: MockRouter, api_key: str, no_fzf: None
) -> None:
    """Ambiguous substring match raises ModelError."""
    mocked_api.get(f"{API_BASE}/models").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "gpt-oss:20b"}, {"id": "gpt-oss:120b"}]}
        )
    )
    with pytest.raises(ModelError, match="Ambiguous"):
        _resolve_model("oss", api_key, API_BASE)


def test_resolve_model_unknown_raises_model_error(
    mocked_api: MockRouter, api_key: str, no_fzf: None
) -> None:
    """No match at all raises ModelError listing available models."""
    mocked_api.get(f"{API_BASE}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-oss:20b"}]})
    )
    with pytest.raises(ModelError, match="Unknown model"):
        _resolve_model("nonexistent", api_key, API_BASE)


# ── RzobModel.execute ─────────────────────────────────────────


def test_execute_non_streaming(
    mocked_api: MockRouter,
    rzob_model: RzobModel,
    simple_prompt: llm.Prompt,
    simple_response: llm.Response,
    api_key: str,
) -> None:
    """Non-streaming execute returns content from API response."""
    mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Hello there!"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
    )
    chunks = list(rzob_model.execute(simple_prompt, False, simple_response, None, api_key))
    assert chunks == ["Hello there!"]
    assert simple_response.response_json is not None


def test_execute_streaming(
    mocked_api: MockRouter,
    rzob_model: RzobModel,
    simple_prompt: llm.Prompt,
    simple_response: llm.Response,
    api_key: str,
) -> None:
    """Streaming execute yields content deltas via SSE."""
    payload = _sse_stream(
        [
            '{"choices":[{"delta":{"content":"Hi"}}]}',
            '{"choices":[{"delta":{"content":" there"}}]}',
            "[DONE]",
        ]
    )
    mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=payload,
            headers={"Content-Type": "text/event-stream"},
        )
    )
    chunks = list(rzob_model.execute(simple_prompt, True, simple_response, None, api_key))
    assert chunks == ["Hi", " there"]


def test_execute_non_streaming_sets_usage(
    mocked_api: MockRouter,
    rzob_model: RzobModel,
    simple_prompt: llm.Prompt,
    simple_response: llm.Response,
    api_key: str,
) -> None:
    """Non-streaming execute calls response.set_usage() with token counts."""
    mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )
    )
    list(rzob_model.execute(simple_prompt, False, simple_response, None, api_key))
    assert simple_response.input_tokens == 10
    assert simple_response.output_tokens == 5
    assert simple_response.token_details == {"total_tokens": 15}


def test_execute_non_streaming_finish_reason_in_response_json(
    mocked_api: MockRouter,
    rzob_model: RzobModel,
    simple_prompt: llm.Prompt,
    simple_response: llm.Response,
    api_key: str,
) -> None:
    """Non-streaming execute preserves finish_reason in response_json."""
    mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    )
    list(rzob_model.execute(simple_prompt, False, simple_response, None, api_key))
    assert simple_response.response_json is not None
    choices = simple_response.response_json["choices"]
    assert choices[0]["finish_reason"] == "stop"


def test_execute_streaming_sets_usage(
    mocked_api: MockRouter,
    rzob_model: RzobModel,
    simple_prompt: llm.Prompt,
    simple_response: llm.Response,
    api_key: str,
) -> None:
    """Streaming execute captures usage from final SSE event."""
    payload = _sse_stream(
        [
            '{"choices":[{"delta":{"content":"Hi"}}]}',
            '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
            '{"usage":{"prompt_tokens":8,"completion_tokens":3,"total_tokens":11}}',
            "[DONE]",
        ]
    )
    mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=payload,
            headers={"Content-Type": "text/event-stream"},
        )
    )
    list(rzob_model.execute(simple_prompt, True, simple_response, None, api_key))
    assert simple_response.input_tokens == 8
    assert simple_response.output_tokens == 3
    assert simple_response.token_details == {"total_tokens": 11}


def test_execute_streaming_sets_response_json(
    mocked_api: MockRouter,
    rzob_model: RzobModel,
    simple_prompt: llm.Prompt,
    simple_response: llm.Response,
    api_key: str,
) -> None:
    """Streaming execute sets response_json with finish_reason and usage."""
    payload = _sse_stream(
        [
            '{"choices":[{"delta":{"content":"Hi"}}]}',
            '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
            '{"usage":{"prompt_tokens":4,"completion_tokens":1,"total_tokens":5}}',
            "[DONE]",
        ]
    )
    mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=payload,
            headers={"Content-Type": "text/event-stream"},
        )
    )
    list(rzob_model.execute(simple_prompt, True, simple_response, None, api_key))
    assert simple_response.response_json is not None
    assert simple_response.response_json["finish_reason"] == "stop"
    assert simple_response.response_json["usage"]["prompt_tokens"] == 4


def test_execute_non_streaming_no_usage_graceful(
    mocked_api: MockRouter,
    rzob_model: RzobModel,
    simple_prompt: llm.Prompt,
    simple_response: llm.Response,
    api_key: str,
) -> None:
    """Non-streaming execute works without usage data in API response."""
    mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}]},
        )
    )
    chunks = list(rzob_model.execute(simple_prompt, False, simple_response, None, api_key))
    assert chunks == ["Hi"]
    assert simple_response.input_tokens is None
    assert simple_response.output_tokens is None


def test_execute_empty_choices_raises_api_error(
    mocked_api: MockRouter,
    rzob_model: RzobModel,
    simple_prompt: llm.Prompt,
    simple_response: llm.Response,
    api_key: str,
) -> None:
    """Non-streaming response with empty choices raises ApiError."""
    mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    with pytest.raises(ApiError, match="Empty response"):
        list(rzob_model.execute(simple_prompt, False, simple_response, None, api_key))


def test_execute_with_options(
    mocked_api: MockRouter,
    rzob_model: RzobModel,
    simple_response: llm.Response,
    api_key: str,
) -> None:
    """Options (temperature, max_tokens) are forwarded in the request body."""
    route = mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )
    prompt = llm.Prompt(
        "test",
        model=rzob_model,
        system="You are a test assistant",
        options=RzobModel.Options(temperature=0.5, max_tokens=100),
    )
    list(rzob_model.execute(prompt, False, simple_response, None, api_key))
    body = json.loads(route.calls[0].request.content)
    assert body["temperature"] == 0.5
    assert body["max_tokens"] == 100
    assert body["model"] == "gpt-oss:20b"
    assert any(m["role"] == "system" for m in body["messages"])


# ── RzobEmbeddingModel.embed_batch ────────────────────────────


def test_embed_batch_single_text(
    mocked_api: MockRouter, embed_model: RzobEmbeddingModel, api_key_env: None
) -> None:
    """Embedding a single text returns one embedding vector."""
    mocked_api.post(f"{API_BASE}/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})
    )
    results = list(embed_model.embed_batch(iter(["hello"])))
    assert len(results) == 1
    assert results[0] == [0.1, 0.2, 0.3]


def test_embed_batch_multiple_texts(
    mocked_api: MockRouter, embed_model: RzobEmbeddingModel, api_key_env: None
) -> None:
    """Embedding multiple texts returns vectors in order."""
    mocked_api.post(f"{API_BASE}/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1, 0.2]},
                    {"embedding": [0.3, 0.4]},
                    {"embedding": [0.5, 0.6]},
                ]
            },
        )
    )
    results = list(embed_model.embed_batch(iter(["a", "b", "c"])))
    assert len(results) == 3
    assert results[1] == [0.3, 0.4]


def test_embed_batch_sends_model_and_input(
    mocked_api: MockRouter, embed_model: RzobEmbeddingModel, api_key_env: None
) -> None:
    """Embedding request includes model ID and input texts."""
    route = mocked_api.post(f"{API_BASE}/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1]}]})
    )
    list(embed_model.embed_batch(iter(["test input"])))
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "bge-m3:567m"
    assert body["input"] == ["test input"]


# ── register_models ───────────────────────────────────────────


def test_register_models_from_cache(cached_models: list[dict], user_dir: Path) -> None:
    """register_models registers all cached models."""
    registered: list[object] = []
    register_models(lambda m, **kw: registered.append(m))
    assert len(registered) > 0
    model_ids = [m.model_id for m in registered]
    assert "fcio-rzob/gpt-oss-20b" in model_ids


def test_register_models_empty_cache(user_dir: Path) -> None:
    """register_models with no cache fallbackt auf _HARD_CODED_MODELS."""
    registered: list[object] = []
    register_models(lambda m, **kw: registered.append(m))
    # 3 locations × 2 hard-coded chat models = 6
    assert len(registered) == 6
    model_ids = [m.model_id for m in registered]
    assert "fcio-rzob/gpt-oss-20b" in model_ids
    assert "fcio-dev/gpt-oss-120b" in model_ids
    assert "fcio-whq/gpt-oss-20b" in model_ids


def test_register_models_includes_aliases(cached_models: list[dict], user_dir: Path) -> None:
    """Registered models have safe_id aliases set."""
    registered: list[object] = []
    register_models(lambda m, **kw: registered.append(m))
    first = registered[0]
    assert first.model_id == "fcio-rzob/gpt-oss-20b"


# ── register_embedding_models ─────────────────────────────────


def test_register_embedding_models_filters(cached_models: list[dict], user_dir: Path) -> None:
    """register_embedding_models only registers models with embed keywords."""
    registered: list[object] = []
    register_embedding_models(lambda m, **kw: registered.append(m))
    model_ids = [m.model_id for m in registered]
    # bge-m3 and Nomic-embed-text have embed keywords
    assert any("bge" in mid for mid in model_ids)
    # gpt-oss models should NOT be registered as embedding models
    assert not any("gpt-oss" in mid for mid in model_ids)


def test_register_embedding_models_empty_cache(user_dir: Path) -> None:
    """register_embedding_models with no cache fallbackt auf _HARD_CODED_MODELS."""
    registered: list[object] = []
    register_embedding_models(lambda m, **kw: registered.append(m))
    # 3 locations × 3 hard-coded embedding models = 9
    assert len(registered) == 9
    model_ids = [m.model_id for m in registered]
    assert "fcio-rzob/bge-m3-567m" in model_ids
    assert "fcio-dev/nomic-embed-text-v1_5" in model_ids
    assert "fcio-whq/embeddinggemma-300m" in model_ids


# ── _load_models ──────────────────────────────────────────────


def test_load_models_from_cache(cached_models: list[dict], user_dir: Path) -> None:
    """_load_models reads models from cache file."""
    models = _load_models("rzob")
    assert len(models) == 4
    assert models[0]["id"] == "gpt-oss:20b"


def test_load_models_empty_when_no_cache(user_dir: Path) -> None:
    """_load_models returns empty list when cache doesn't exist."""
    models = _load_models("rzob")
    assert models == []


# ── execute with conversation ────────────────────────────────


def test_execute_with_conversation(
    mocked_api: MockRouter,
    rzob_model: RzobModel,
    simple_response: llm.Response,
    api_key: str,
) -> None:
    """Execute with conversation history includes all messages."""
    mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )
    conv_response = llm.Response(
        model=rzob_model,
        prompt=llm.Prompt(
            "Hello", model=rzob_model, options=RzobModel.Options(), system="prev system"
        ),
        stream=False,
    )
    conv_response._text = "Hi there"  # ty: ignore[attr-defined]
    conv = llm.Conversation(responses=[conv_response], model=rzob_model)
    prompt = llm.Prompt("How are you?", model=rzob_model, options=RzobModel.Options())
    chunks = list(rzob_model.execute(prompt, False, simple_response, conv, api_key))
    assert chunks == ["ok"]
    req_body = json.loads(mocked_api.calls.last.request.content)
    # Should have system + user + assistant from conversation + user from prompt
    roles = [m["role"] for m in req_body["messages"]]
    assert "system" in roles
    assert "assistant" in roles


def test_execute_with_attachment(
    mocked_api: MockRouter,
    rzob_model: RzobModel,
    simple_response: llm.Response,
    api_key: str,
) -> None:
    """Execute with text/plain attachment includes attachment content."""
    mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )
    att = llm.Attachment(type="text/plain", content=b"attached text content")
    prompt = llm.Prompt(
        "Summarize", model=rzob_model, options=RzobModel.Options(), attachments=[att]
    )
    chunks = list(rzob_model.execute(prompt, False, simple_response, None, api_key))
    assert chunks == ["ok"]
    req_body = json.loads(mocked_api.calls.last.request.content)
    last_msg = req_body["messages"][-1]
    # With attachments, content is a list of parts
    assert isinstance(last_msg["content"], list)


def test_execute_with_all_options(
    mocked_api: MockRouter,
    rzob_model: RzobModel,
    simple_response: llm.Response,
    api_key: str,
) -> None:
    """Execute with top_p, tools, response_format options forwarded."""
    route = mocked_api.post(f"{API_BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )
    prompt = llm.Prompt(
        "test",
        model=rzob_model,
        options=RzobModel.Options(
            top_p=0.9,
            tools=[{"type": "function", "function": {"name": "test"}}],
            response_format={"type": "json_object"},
        ),
    )
    list(rzob_model.execute(prompt, False, simple_response, None, api_key))
    body = json.loads(route.calls[0].request.content)
    assert body["top_p"] == 0.9
    assert body["tools"] == [{"type": "function", "function": {"name": "test"}}]
    assert body["response_format"] == {"type": "json_object"}


# ── __str__ ─────────────────────────────────────────────────


def test_model_str(rzob_model: RzobModel) -> None:
    """RzobModel.__str__ returns expected format."""
    assert str(rzob_model) == "Flying Circus: fcio-rzob/gpt-oss-20b"


# ── register_models with short aliases ──────────────────────


def test_register_models_includes_short_aliases(cached_models: list[dict], user_dir: Path) -> None:
    """register_models registers short aliases for known models."""
    registered_pairs: list[tuple[object, dict]] = []
    register_models(lambda m, **kw: registered_pairs.append((m, kw)))
    # Find the gpt-oss:20b model registration
    for model, kwargs in registered_pairs:
        if model.api_id == "gpt-oss:20b":
            aliases = kwargs.get("aliases", [])
            assert "20b" in aliases
            break
    else:
        pytest.fail("gpt-oss:20b not registered")


# ── register_models multi-location ──────────────────────────


def test_register_models_non_rzob_no_short_alias(
    user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-rzob locations don't get short aliases."""
    dev_cache = user_dir / "fcio_models_dev.json"
    dev_cache.write_text(json.dumps([{"id": "gpt-oss:20b", "safe_id": "gpt-oss-20b"}]))
    registered_pairs: list[tuple[object, dict]] = []
    register_models(lambda m, **kw: registered_pairs.append((m, kw)))
    for model, kwargs in registered_pairs:
        if model.model_id.startswith("fcio-dev"):
            aliases = kwargs.get("aliases", [])
            assert "20b" not in aliases
            break
