"""Contract tests for elevation-proposal impl spec (Q2, Q3, S1, S2, A2).

Q2/Q3 tests validate existing code paths (no longer xfail).
S1/S2/A2 test functions/classes created by Phase 2 refactoring.
"""

import inspect
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════
# Q2: collect_code_files() non-directory early return (line 759)
# ═══════════════════════════════════════════════════════════════════


def test_collect_code_files_nonexistent_path_returns_empty() -> None:
    """Q2 contract: collect_code_files(Path("/nonexistent")) returns [].

    Validates the early-return on line 759 when the input path is not a
    directory. The function must return an empty list without error.
    """
    from llm_fcio import collect_code_files

    result = collect_code_files(Path("/nonexistent"))
    assert result == []


def test_collect_code_files_file_path_returns_empty() -> None:
    """Q2 contract: collect_code_files with a file path (not dir) returns [].

    Passing a regular file instead of a directory must return [] via the
    same early-return path (line 758-759).
    """
    from llm_fcio import collect_code_files

    result = collect_code_files(Path(__file__))
    assert result == []


# ═══════════════════════════════════════════════════════════════════
# Q3: install_renderer_patch() patched iterator execution
# ═══════════════════════════════════════════════════════════════════


def test_install_renderer_patch_idempotent() -> None:
    """Q3 contract: calling install_renderer_patch() twice is idempotent.

    The second call must hit the _fcio_patched guard (line 581) and not
    re-wrap __iter__. Both calls result in the same patched function.
    """
    import llm

    import llm_fcio

    original_iter = llm.Response.__iter__

    try:
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            llm_fcio.install_renderer_patch()
            first_patched = llm.Response.__iter__

            llm_fcio.install_renderer_patch()
            second_patched = llm.Response.__iter__

            assert first_patched is second_patched
            assert getattr(first_patched, "_fcio_patched", False) is True
    finally:
        llm.Response.__iter__ = original_iter


def test_patched_iter_yields_chunks_and_calls_renderer() -> None:
    """Q3 contract: _patched_iter yields chunks and feeds the renderer.

    Installs the patch with TTY=True, creates a mock llm.Response whose
    original __iter__ yields chunks. Iterating the patched __iter__ must
    yield those same chunks and call renderer.feed() on each.
    """
    import llm

    import llm_fcio

    original_iter = llm.Response.__iter__
    chunks = ["Hello ", "world", "!"]

    def fake_iter(self: Any) -> Any:  # noqa: ANN401
        yield from chunks

    try:
        with (
            patch("sys.stdout") as mock_stdout,
            patch.object(llm_fcio._StreamingRenderer, "feed") as mock_feed,
            patch.object(llm_fcio._StreamingRenderer, "flush"),
        ):
            mock_stdout.isatty.return_value = True
            llm.Response.__iter__ = fake_iter
            llm_fcio.install_renderer_patch()

            mock_response = MagicMock(spec=llm.Response)
            collected = list(llm.Response.__iter__(mock_response))

            assert collected == chunks
            assert mock_feed.call_count >= 1
    finally:
        llm.Response.__iter__ = original_iter


def test_patched_iter_renderer_exception_falls_back() -> None:
    """Q3 contract: _StreamingRenderer() raising falls back to original iter.

    When the renderer constructor (line 585) raises, the patched iterator
    must fall back to yielding directly from the original __iter__ (lines
    586-588) instead of crashing.
    """
    import llm

    import llm_fcio

    original_iter = llm.Response.__iter__
    chunks = ["fallback ", "works"]

    def fake_iter(self: Any) -> Any:  # noqa: ANN401
        yield from chunks

    try:
        with (
            patch("sys.stdout") as mock_stdout,
            patch("llm_fcio._StreamingRenderer", side_effect=OSError("no tty")),
        ):
            mock_stdout.isatty.return_value = True
            llm.Response.__iter__ = fake_iter
            llm_fcio.install_renderer_patch()

            mock_response = MagicMock(spec=llm.Response)
            collected = list(llm.Response.__iter__(mock_response))

            assert collected == chunks
    finally:
        llm.Response.__iter__ = original_iter


# ═══════════════════════════════════════════════════════════════════
# S1: _make_client() complexity reduction — extracted helpers
# ═══════════════════════════════════════════════════════════════════


def test_log_request_body_exists() -> None:
    """S1 contract: _log_request_body(content: bytes) -> None exists.

    Phase 2 extracts the request-body logging from _make_client's
    _on_request closure into a standalone function.
    """
    from llm_fcio import _log_request_body

    assert callable(_log_request_body)


def test_log_request_body_handles_json() -> None:
    """S1 contract: _log_request_body pretty-prints valid JSON bodies."""
    from llm_fcio import _log_request_body

    # Should not raise on valid JSON content
    _log_request_body(b'{"key": "value"}')


def test_log_request_body_handles_raw() -> None:
    """S1 contract: _log_request_body handles non-JSON raw bytes."""
    from llm_fcio import _log_request_body

    # Should not raise on non-JSON content
    _log_request_body(b"not json at all")


def test_log_request_body_handles_truncation() -> None:
    """S1 contract: _log_request_body truncates long raw bodies."""
    from llm_fcio import _log_request_body

    # Should not raise on very long content
    _log_request_body(b"x" * 10000)


def test_log_response_body_exists() -> None:
    """S1 contract: _log_response_body(response: httpx.Response) -> None exists.

    Phase 2 extracts the response-body logging from _make_client's
    _on_response closure into a standalone function.
    """
    from llm_fcio import _log_response_body

    assert callable(_log_response_body)


def test_log_response_body_handles_sse() -> None:
    """S1 contract: _log_response_body short-circuits on SSE content-type."""
    import httpx

    from llm_fcio import _log_response_body

    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text="data: hello\n",
    )
    # Should not raise — SSE stream handling is a short-circuit path
    _log_response_body(response)


def test_log_response_body_handles_json() -> None:
    """S1 contract: _log_response_body pretty-prints JSON response bodies."""
    import httpx

    from llm_fcio import _log_response_body

    response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "hi"}}]},
    )
    _log_response_body(response)


def test_log_response_body_handles_empty() -> None:
    """S1 contract: _log_response_body handles empty response bodies."""
    import httpx

    from llm_fcio import _log_response_body

    response = httpx.Response(204)
    _log_response_body(response)


def test_log_response_body_handles_non_json() -> None:
    """S1 contract: _log_response_body handles non-JSON response bodies."""
    import httpx

    from llm_fcio import _log_response_body

    response = httpx.Response(
        200,
        text="<html>not json</html>",
        headers={"content-type": "text/html"},
    )
    _log_response_body(response)


def test_mask_auth_header_exists() -> None:
    """S1 contract: _mask_auth_header(name, value) -> str exists.

    Phase 2 extracts the auth-header masking logic from _on_request into
    a standalone function.
    """
    from llm_fcio import _mask_auth_header

    assert callable(_mask_auth_header)


def test_mask_auth_header_masks_bearer() -> None:
    """S1 contract: _mask_auth_header masks Authorization Bearer tokens."""
    from llm_fcio import _mask_auth_header

    result = _mask_auth_header("authorization", "Bearer sk-secret123")
    assert "sk-secret123" not in result
    assert "***" in result or "..." in result


def test_mask_auth_header_passes_through_other_headers() -> None:
    """S1 contract: _mask_auth_header passes non-auth headers unchanged."""
    from llm_fcio import _mask_auth_header

    result = _mask_auth_header("content-type", "application/json")
    assert result == "application/json"


def test_make_client_cyclomatic_complexity_within_threshold() -> None:
    """S1 contract: _make_client CC <= 15 after refactoring.

    Phase 2 must reduce cyclomatic complexity from CC=47 to ≤15 by
    extracting helper functions. Verified by counting branch points in
    the function source.
    """
    from llm_fcio import _make_client

    source = inspect.getsource(_make_client)
    # Count branch-inducing keywords as a proxy for CC
    branch_keywords = (
        source.count(" if ")
        + source.count("\nif ")
        + source.count("elif ")
        + source.count("else:")
        + source.count("except ")
        + source.count("for ")
        + source.count("while ")
        + source.count("and ")
        + source.count("or ")
    )
    # CC = branches + 1; allow generous margin since keyword counting is
    # approximate, but the threshold is 15
    assert branch_keywords + 1 <= 15, (
        f"_make_client CC ~{branch_keywords + 1}, exceeds threshold of 15"
    )


# ═══════════════════════════════════════════════════════════════════
# S2: execute() and _send_chat_request() complexity reduction
# ═══════════════════════════════════════════════════════════════════


def test_build_messages_exists() -> None:
    """S2 contract: _build_messages(prompt, conversation) -> list[dict] exists.

    Phase 2 extracts message-building logic from execute() into a
    standalone helper.
    """
    from llm_fcio import _build_messages

    assert callable(_build_messages)


def test_build_messages_system_prompt() -> None:
    """S2 contract: _build_messages includes system message when present."""
    from unittest.mock import MagicMock

    from llm_fcio import _build_messages

    prompt = MagicMock()
    prompt.system = "You are helpful."
    prompt.prompt = "Hello"
    prompt.attachments = []

    messages = _build_messages(prompt, conversation=None)
    assert any(
        m.get("role") == "system" and m.get("content") == "You are helpful." for m in messages
    )


def test_build_messages_user_prompt() -> None:
    """S2 contract: _build_messages includes user message from prompt."""
    from unittest.mock import MagicMock

    from llm_fcio import _build_messages

    prompt = MagicMock()
    prompt.system = None
    prompt.prompt = "What is 2+2?"
    prompt.attachments = []

    messages = _build_messages(prompt, conversation=None)
    assert any(m.get("role") == "user" and "2+2" in str(m.get("content")) for m in messages)


def test_auth_headers_exists() -> None:
    """S2 contract: _auth_headers(key) -> dict exists.

    Phase 2 extracts the auth header construction from execute() into a
    standalone helper.
    """
    from llm_fcio import _auth_headers

    assert callable(_auth_headers)


def test_auth_headers_returns_correct_structure() -> None:
    """S2 contract: _auth_headers returns Bearer auth + JSON content-type."""
    from llm_fcio import _auth_headers

    headers = _auth_headers("test-key-123")
    assert headers == {
        "Authorization": "Bearer test-key-123",
        "Content-Type": "application/json",
    }


def test_extract_content_exists() -> None:
    """S2 contract: _extract_content(data) -> str exists.

    Phase 2 extracts the content extraction from execute() and
    _send_chat_request() into a standalone helper.
    """
    from llm_fcio import _extract_content

    assert callable(_extract_content)


def test_extract_content_extracts_from_valid_response() -> None:
    """S2 contract: _extract_content returns content from valid API data."""
    from llm_fcio import _extract_content

    data = {
        "choices": [
            {"message": {"content": "Hello from API"}},
        ]
    }
    result = _extract_content(data)
    assert result == "Hello from API"


def test_extract_content_raises_on_empty_choices() -> None:
    """S2 contract: _extract_content raises ApiError on empty choices."""
    from llm_fcio import ApiError, _extract_content

    with pytest.raises(ApiError, match="no choices"):
        _extract_content({"choices": []})


def test_extract_content_raises_on_missing_choices() -> None:
    """S2 contract: _extract_content raises ApiError when choices is absent."""
    from llm_fcio import ApiError, _extract_content

    with pytest.raises(ApiError):
        _extract_content({})


def test_execute_cyclomatic_complexity_within_threshold() -> None:
    """S2 contract: execute() CC <= 15 after refactoring.

    Phase 2 must reduce cyclomatic complexity from CC=33 to ≤15 by
    extracting _build_messages, _auth_headers, _extract_content.
    """
    from llm_fcio import RzobModel

    source = inspect.getsource(RzobModel.execute)
    branch_keywords = (
        source.count(" if ")
        + source.count("\nif ")
        + source.count("elif ")
        + source.count("else:")
        + source.count("except ")
        + source.count("for ")
        + source.count("while ")
        + source.count("and ")
        + source.count("or ")
    )
    assert branch_keywords + 1 <= 15, f"execute CC ~{branch_keywords + 1}, exceeds threshold of 15"


def test_send_chat_request_cyclomatic_complexity_within_threshold() -> None:
    """S2 contract: _send_chat_request CC <= 15 after refactoring.

    Phase 2 must reduce cyclomatic complexity from CC=31 to ≤15.
    """
    from llm_fcio import _send_chat_request

    source = inspect.getsource(_send_chat_request)
    branch_keywords = (
        source.count(" if ")
        + source.count("\nif ")
        + source.count("elif ")
        + source.count("else:")
        + source.count("except ")
        + source.count("for ")
        + source.count("while ")
        + source.count("and ")
        + source.count("or ")
    )
    assert branch_keywords + 1 <= 15, (
        f"_send_chat_request CC ~{branch_keywords + 1}, exceeds threshold of 15"
    )


# ═══════════════════════════════════════════════════════════════════
# A2: Adapter fakes for CLI test layer
# ═══════════════════════════════════════════════════════════════════


def test_fake_collection_exists() -> None:
    """A2 contract: FakeCollection class exists in test infrastructure.

    Must have: model(), embed_multi(), exists() classmethod.
    Replaces bare MagicMock() usage in CLI tests.
    """
    from tests.fakes import FakeCollection

    assert hasattr(FakeCollection, "model")
    assert hasattr(FakeCollection, "embed_multi")
    assert hasattr(FakeCollection, "exists")


def test_fake_collection_exists_is_classmethod() -> None:
    """A2 contract: FakeCollection.exists is a classmethod."""
    from tests.fakes import FakeCollection

    assert isinstance(
        inspect.getattr_static(FakeCollection, "exists"),
        classmethod,
    )


def test_fake_response_exists_and_yields_chunks() -> None:
    """A2 contract: FakeResponse with __iter__ yielding configurable chunks.

    Replaces bare MagicMock() in streaming tests.
    """
    from tests.fakes import FakeResponse

    chunks = ["chunk1", "chunk2", "chunk3"]
    resp = FakeResponse(chunks)
    collected = list(resp)
    assert collected == chunks


def test_fake_model_exists_and_returns_fake_response() -> None:
    """A2 contract: FakeModel.prompt() returns a FakeResponse.

    Replaces bare MagicMock() in model-execution tests.
    """
    from tests.fakes import FakeModel, FakeResponse

    model = FakeModel()
    result = model.prompt("test input")
    assert isinstance(result, FakeResponse)


def test_fake_collection_model_returns_fake_model() -> None:
    """A2 contract: FakeCollection.model() returns a usable model instance."""
    from tests.fakes import FakeCollection, FakeModel

    collection = FakeCollection()
    model = collection.model("test-model")
    assert isinstance(model, FakeModel)


def test_fakes_usable_as_drop_in_replacement() -> None:
    """A2 contract: fakes work as drop-in replacements in a test scenario.

    Simulates the usage pattern: collection -> model -> prompt -> iterate.
    """
    from tests.fakes import FakeCollection

    collection = FakeCollection()
    model = collection.model("gpt-test")
    response = model.prompt("hello")
    chunks = list(response)
    assert isinstance(chunks, list)
    assert all(isinstance(c, str) for c in chunks)
