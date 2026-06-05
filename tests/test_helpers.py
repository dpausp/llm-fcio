"""Tests for helper functions in llm_fcio.

Covers _b32c_encode, _generate_lid, _mask_auth_header, _extract_content,
_chunk_lines, _discover_files, _build_chat_body, _build_messages,
_auth_headers, collect_code_files, and model registration.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import click
import pytest

from llm_fcio import (
    ApiError,
    _auth_headers,
    _b32c_encode,
    _build_chat_body,
    _build_messages,
    _chunk_lines,
    _discover_files,
    _extract_content,
    _generate_lid,
    _mask_auth_header,
    collect_code_files,
    register_embedding_models,
    register_models,
)

# ═══════════════════════════════════════════════════════════════════
# Encoding & ID generation
# ═══════════════════════════════════════════════════════════════════


def test_b32c_encode_known_values() -> None:
    """Bekannte Integer -> base32-crockford strings."""
    cases: list[tuple[int, int, str]] = [
        (0, 1, "0"),
        (0, 5, "00000"),
        (1, 1, "1"),
        (31, 1, "Z"),
        (32, 2, "10"),
        (42, 2, "1A"),
        # 64-bit all-ones encoded as 13 chars: F + 12xZ (nur 64 Bits = 13x5 - 1 padding)
        (0xFFFF_FFFF_FFFF_FFFF, 13, "FZZZZZZZZZZZZ"),
    ]
    for value, length, expected in cases:
        assert _b32c_encode(value, length) == expected, (
            f"_b32c_encode({value}, {length}) != {expected!r}"
        )


def test_b32c_encode_zero_length() -> None:
    """Länge 0 -> leerer String."""
    assert _b32c_encode(12345, 0) == ""


def test_b32c_encode_reversible() -> None:
    """Gleicher Input -> gleicher Output."""
    assert _b32c_encode(999, 5) == _b32c_encode(999, 5)


def test_b32c_encode_lsb_is_rightmost() -> None:
    """Niederwertigste 5 Bits sind das letzte Zeichen (nach Reverse)."""
    # value=32 (0b100000): LSB=0, next=1 -> "10"
    assert _b32c_encode(32, 2) == "10"
    # value=33 (0b100001): LSB=1, next=1 -> "11"
    assert _b32c_encode(33, 2) == "11"


_LID_PATTERN = re.compile(r"^[0-9A-Z]{9}-[0-9A-Z]{4}$")


def test_generate_lid_format() -> None:
    """LID hat Format XXXXXXXXX-XXXX, nur base32-crockford."""
    lid = _generate_lid()
    assert _LID_PATTERN.match(lid), f"LID format mismatch: {lid!r}"


def test_generate_lid_length() -> None:
    """LID ist genau 14 Zeichen (9+1+4)."""
    assert len(_generate_lid()) == 14


def test_generate_lid_only_valid_chars() -> None:
    """LID enthält nur Zeichen aus _B32C plus Bindestrich."""
    lid = _generate_lid()
    valid = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ-")
    assert set(lid) <= valid, f"Invalid chars in LID: {lid!r}"


def test_generate_lid_has_hyphen() -> None:
    """LID enhält genau einen Bindestrich an Position 9."""
    lid = _generate_lid()
    assert lid[9] == "-"
    assert lid.count("-") == 1


def test_generate_lid_unique_across_calls() -> None:
    """Zwei aufeinanderfolgende LIDs sind unterschiedlich (ms-Basis)."""
    lids = {_generate_lid() for _ in range(10)}
    assert len(lids) == 10, "LIDs sollten unique sein"


# ═══════════════════════════════════════════════════════════════════
# Auth & content helpers
# ═══════════════════════════════════════════════════════════════════


def test_mask_auth_header_masks_authorization() -> None:
    """Authorization-Header wird maskiert."""
    assert _mask_auth_header("Authorization", "Bearer sk-real-key-123") == "Bearer sk-***..."


def test_mask_auth_header_masks_authorization_lowercase() -> None:
    """Case-insensitive: 'authorization' wird auch maskiert."""
    assert _mask_auth_header("authorization", "Bearer sk-real-key-456") == "Bearer sk-***..."


def test_mask_auth_header_passes_others() -> None:
    """Nicht-Authorization-Header werden unverändert durchgelassen."""
    cases: list[tuple[str, str]] = [
        ("Content-Type", "application/json"),
        ("X-Debug-ID", "abc-123"),
        ("Accept", "text/plain"),
        ("", "anything"),
    ]
    for name, value in cases:
        assert _mask_auth_header(name, value) == value, (
            f"Header {name!r} sollte unverändert bleiben"
        )


def test_auth_headers_returns_bearer_and_json() -> None:
    """_auth_headers returns Bearer auth + JSON content-type."""
    headers = _auth_headers("test-key-123")
    assert headers == {
        "Authorization": "Bearer test-key-123",
        "Content-Type": "application/json",
    }


def test_extract_content_normal() -> None:
    """Normaler Fall: choices[0].message.content."""
    data = {"choices": [{"message": {"content": "Hello world"}}]}
    assert _extract_content(data) == "Hello world"


def test_extract_content_empty_string() -> None:
    """content ist leerer String."""
    data = {"choices": [{"message": {"content": ""}}]}
    assert _extract_content(data) == ""


def test_extract_content_missing_content_key() -> None:
    """message dict hat kein 'content'."""
    data = {"choices": [{"message": {"role": "assistant"}}]}
    assert _extract_content(data) == ""


def test_extract_content_missing_message() -> None:
    """choices[0] hat kein 'message'."""
    data = {"choices": [{"finish_reason": "stop"}]}
    assert _extract_content(data) == ""


def test_extract_content_empty_choices_list() -> None:
    """choices ist leere Liste -> ApiError."""
    data = {"choices": []}
    with pytest.raises(ApiError, match="Empty response"):
        _extract_content(data)


def test_extract_content_missing_choices_key() -> None:
    """dict hat gar keinen 'choices'-Key -> ApiError."""
    data: dict[str, Any] = {"id": "123"}
    with pytest.raises(ApiError, match="Empty response"):
        _extract_content(data)


def test_extract_content_choices_is_none() -> None:
    """choices ist None -> ApiError."""
    data = {"choices": None}
    with pytest.raises(ApiError, match="Empty response"):
        _extract_content(data)


def test_extract_content_preserves_whitespace() -> None:
    """Whitespace im content bleibt erhalten."""
    data = {"choices": [{"message": {"content": "  hello\n  world  "}}]}
    assert _extract_content(data) == "  hello\n  world  "


# ═══════════════════════════════════════════════════════════════════
# Message building
# ═══════════════════════════════════════════════════════════════════


def test_build_messages_includes_system_when_present() -> None:
    """_build_messages includes system message when present."""
    prompt = MagicMock()
    prompt.system = "You are helpful."
    prompt.prompt = "Hello"
    prompt.attachments = []

    messages = _build_messages(prompt, conversation=None)
    assert any(
        m.get("role") == "system" and m.get("content") == "You are helpful." for m in messages
    )


def test_build_messages_includes_user_prompt() -> None:
    """_build_messages includes user message from prompt."""
    prompt = MagicMock()
    prompt.system = None
    prompt.prompt = "What is 2+2?"
    prompt.attachments = []

    messages = _build_messages(prompt, conversation=None)
    assert any(m.get("role") == "user" and "2+2" in str(m.get("content")) for m in messages)


def test_build_messages_no_system_when_absent() -> None:
    """_build_messages omits system message when prompt.system is None."""
    prompt = MagicMock()
    prompt.system = None
    prompt.prompt = "Just asking"
    prompt.attachments = []

    messages = _build_messages(prompt, conversation=None)
    assert not any(m.get("role") == "system" for m in messages)


# ═══════════════════════════════════════════════════════════════════
# Chat body construction
# ═══════════════════════════════════════════════════════════════════


def test_build_chat_body_includes_max_tokens() -> None:
    body = _build_chat_body("gpt-4", [{"role": "user", "content": "hi"}], 0.7, 100)
    assert body["max_tokens"] == 100


def test_build_chat_body_structure_with_max_tokens() -> None:
    msgs = [{"role": "user", "content": "hello"}]
    body = _build_chat_body("gpt-oss-20b", msgs, 0.5, 200)
    assert body == {
        "model": "gpt-oss-20b",
        "messages": msgs,
        "temperature": 0.5,
        "max_tokens": 200,
    }


def test_build_chat_body_omits_max_tokens_when_none() -> None:
    body = _build_chat_body("gpt-4", [{"role": "user", "content": "hi"}], 0.7, None)
    assert "max_tokens" not in body


def test_build_chat_body_structure_without_max_tokens() -> None:
    msgs = [{"role": "user", "content": "hello"}]
    body = _build_chat_body("gpt-oss-20b", msgs, 0.5, None)
    assert body == {
        "model": "gpt-oss-20b",
        "messages": msgs,
        "temperature": 0.5,
    }


def test_build_chat_body_zero_temperature() -> None:
    body = _build_chat_body("m", [], 0.0, None)
    assert body["temperature"] == 0.0


def test_build_chat_body_max_temperature() -> None:
    body = _build_chat_body("m", [], 2.0, 50)
    assert body["temperature"] == 2.0


def test_build_chat_body_empty_messages() -> None:
    body = _build_chat_body("m", [], 0.7, None)
    assert body["messages"] == []


def test_build_chat_body_multiple_messages() -> None:
    msgs = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
        {"role": "user", "content": "How are you?"},
    ]
    body = _build_chat_body("m", msgs, 0.7, 100)
    assert body["messages"] is msgs
    assert len(body["messages"]) == 4


def test_build_chat_body_model_id_preserved() -> None:
    body = _build_chat_body("gpt-oss:20b", [], 0.5, None)
    assert body["model"] == "gpt-oss:20b"


def test_build_chat_body_max_tokens_zero_is_truthy() -> None:
    # _build_chat_body uses `if max_tokens:` which is falsy for 0
    body = _build_chat_body("m", [], 0.5, 0)
    assert "max_tokens" not in body


# ═══════════════════════════════════════════════════════════════════
# File collection
# ═══════════════════════════════════════════════════════════════════


def test_collect_code_files_includes_python_files(tmp_path: Path) -> None:
    """collect_code_files includes .py files via extension whitelist."""
    (tmp_path / "app.py").write_text("print('hello')")
    (tmp_path / "util.py").write_text("def helper(): pass")

    files = collect_code_files(tmp_path)
    names = [f.name for f in files]
    assert "app.py" in names
    assert "util.py" in names


def test_collect_code_files_excludes_non_code_extensions(tmp_path: Path) -> None:
    """collect_code_files excludes non-code extensions by whitelist."""
    (tmp_path / "data.json").write_text("{}")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / "notes.txt").write_text("not code")

    files = collect_code_files(tmp_path)
    names = [f.name for f in files]
    assert "data.json" not in names
    assert "image.png" not in names
    assert "notes.txt" not in names


def test_collect_code_files_respects_gitignore(tmp_path: Path) -> None:
    """collect_code_files respects .gitignore filtering."""
    (tmp_path / ".gitignore").write_text("__pycache__/\n*.pyc\nbuild/\n")
    (tmp_path / "app.py").write_text("print('hello')")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "app.cpython-314.pyc").write_bytes(b"\x00")
    build = tmp_path / "build"
    build.mkdir()
    (build / "output.py").write_text("# generated")

    files = collect_code_files(tmp_path)
    names = [f.name for f in files]
    assert "app.py" in names
    assert "app.cpython-314.pyc" not in names
    assert "output.py" not in names


def test_collect_code_files_empty_directory(tmp_path: Path) -> None:
    """collect_code_files returns empty list for empty directory."""
    empty = tmp_path / "empty_project"
    empty.mkdir()

    files = collect_code_files(empty)
    assert files == []


def test_collect_code_files_nonexistent_path_returns_empty() -> None:
    """collect_code_files returns [] for non-existent path."""
    result = collect_code_files(Path("/nonexistent"))
    assert result == []


# ═══════════════════════════════════════════════════════════════════
# File discovery (_discover_files)
# ═══════════════════════════════════════════════════════════════════


def test_discover_files_single_file(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("hello")
    result = _discover_files((f,), "*.md")
    assert result == [f]


def test_discover_files_multiple_files(tmp_path: Path) -> None:
    f1 = tmp_path / "a.md"
    f2 = tmp_path / "b.md"
    f1.write_text("a")
    f2.write_text("b")
    result = _discover_files((f1, f2), "*.md")
    assert set(result) == {f1, f2}


def test_discover_files_directory_recursive(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    f1 = tmp_path / "root.md"
    f2 = sub / "nested.md"
    f1.write_text("r")
    f2.write_text("n")
    result = _discover_files((tmp_path,), "*.md")
    assert set(result) == {f1, f2}


def test_discover_files_glob_pattern_filters(tmp_path: Path) -> None:
    md_file = tmp_path / "doc.md"
    py_file = tmp_path / "code.py"
    md_file.write_text("md")
    py_file.write_text("py")
    result = _discover_files((tmp_path,), "*.py")
    assert result == [py_file]


def test_discover_files_file_not_matching_glob(tmp_path: Path) -> None:
    f = tmp_path / "code.py"
    f.write_text("code")
    # Single files are included directly without glob filtering
    result = _discover_files((f,), "*.md")
    assert result == [f]


def test_discover_files_nonexistent_path_raises(tmp_path: Path) -> None:
    missing = tmp_path / "no_such_thing"
    with pytest.raises(click.ClickException, match="Path not found"):
        _discover_files((missing,), "*.md")


def test_discover_files_nonexistent_path_in_message(tmp_path: Path) -> None:
    missing = tmp_path / "gone"
    with pytest.raises(click.ClickException, match="gone"):
        _discover_files((missing,), "*.md")


def test_discover_files_gitignore_filtering(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored_dir/\n")
    (tmp_path / "keep.md").write_text("keep")
    ignored_dir = tmp_path / "ignored_dir"
    ignored_dir.mkdir()
    (ignored_dir / "secret.md").write_text("secret")
    result = _discover_files((tmp_path,), "*.md")
    assert result == [tmp_path / "keep.md"]


def test_discover_files_gitignore_wildcard(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "doc.md").write_text("doc")
    (tmp_path / "debug.log").write_text("log")
    result = _discover_files((tmp_path,), "*")
    filenames = {f.name for f in result}
    assert "doc.md" in filenames
    assert "debug.log" not in filenames
    assert ".gitignore" in filenames


def test_discover_files_venv_excluded(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "lib.md").write_text("lib")
    (tmp_path / "keep.md").write_text("keep")
    result = _discover_files((tmp_path,), "*.md")
    assert set(result) == {tmp_path / "keep.md"}


def test_discover_files_pycache_excluded(tmp_path: Path) -> None:
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "mod.md").write_text("mod")
    (tmp_path / "keep.md").write_text("keep")
    result = _discover_files((tmp_path,), "*.md")
    assert set(result) == {tmp_path / "keep.md"}


def test_discover_files_node_modules_excluded(tmp_path: Path) -> None:
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "pkg.md").write_text("pkg")
    (tmp_path / "keep.md").write_text("keep")
    result = _discover_files((tmp_path,), "*.md")
    assert set(result) == {tmp_path / "keep.md"}


def test_discover_files_dot_git_excluded(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "README.md").write_text("readme")
    (tmp_path / "keep.md").write_text("keep")
    result = _discover_files((tmp_path,), "*.md")
    assert set(result) == {tmp_path / "keep.md"}


def test_discover_files_empty_dir_returns_empty(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _discover_files((empty,), "*.md")
    assert result == []


def test_discover_files_no_matching_files_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text("code")
    result = _discover_files((tmp_path,), "*.md")
    assert result == []


# ═══════════════════════════════════════════════════════════════════
# Text chunking
# ═══════════════════════════════════════════════════════════════════


def test_chunk_lines_empty_string() -> None:
    assert _chunk_lines("", "f.py", 10, 2) == []


def test_chunk_lines_only_newlines() -> None:
    # splitlines() on "\n\n\n" produces ['','',''] -- not empty
    result = _chunk_lines("\n\n\n", "f.py", 10, 2)
    assert len(result) == 1
    assert result[0] == ("f.py:1-3", "\n\n")


def test_chunk_lines_one_line() -> None:
    result = _chunk_lines("hello", "f.py", 10, 2)
    assert len(result) == 1
    assert result[0] == ("f.py:1-1", "hello")


def test_chunk_lines_one_line_trailing_newline() -> None:
    result = _chunk_lines("hello\n", "f.py", 10, 2)
    # splitlines() ignores trailing newline -- single line
    assert len(result) == 1
    assert result[0] == ("f.py:1-1", "hello")


def test_chunk_lines_exact_fit_one_chunk() -> None:
    text = "\n".join(f"line{i}" for i in range(5))
    result = _chunk_lines(text, "f.py", 5, 0)
    assert len(result) == 1
    assert result[0][0] == "f.py:1-5"


def test_chunk_lines_exact_fit_two_chunks_no_overlap() -> None:
    text = "\n".join(f"line{i}" for i in range(10))
    result = _chunk_lines(text, "f.py", 5, 0)
    assert len(result) == 2
    assert result[0][0] == "f.py:1-5"
    assert result[1][0] == "f.py:6-10"


def test_chunk_lines_exact_fit_two_chunks_content() -> None:
    text = "\n".join(f"line{i}" for i in range(10))
    result = _chunk_lines(text, "f.py", 5, 0)
    assert result[0][1] == "line0\nline1\nline2\nline3\nline4"
    assert result[1][1] == "line5\nline6\nline7\nline8\nline9"


def test_chunk_lines_overlap_produces_three_chunks() -> None:
    text = "\n".join(f"line{i}" for i in range(10))
    # chunk_size=5, overlap=2 -> step=3
    result = _chunk_lines(text, "f.py", 5, 2)
    assert len(result) == 3
    assert result[0][0] == "f.py:1-5"
    assert result[1][0] == "f.py:4-8"
    assert result[2][0] == "f.py:7-10"


def test_chunk_lines_overlap_content_shared() -> None:
    text = "\n".join(f"line{i}" for i in range(7))
    # chunk_size=4, overlap=1 -> step=3
    result = _chunk_lines(text, "f.py", 4, 1)
    assert len(result) == 2
    # Chunk 1: lines 0-3
    assert "line3" in result[0][1]
    # Chunk 2: lines 3-6 (overlaps on line3)
    assert "line3" in result[1][1]
    assert "line6" in result[1][1]


def test_chunk_lines_overlap_equal_to_chunk_size_raises() -> None:
    with pytest.raises(ValueError, match="overlap"):
        _chunk_lines("a\nb\nc", "f.py", 5, 5)


def test_chunk_lines_overlap_greater_than_chunk_size_raises() -> None:
    with pytest.raises(ValueError, match="overlap"):
        _chunk_lines("a\nb\nc", "f.py", 3, 10)


def test_chunk_lines_zero_chunk_size_raises() -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        _chunk_lines("a\nb", "f.py", 0, 0)


def test_chunk_lines_negative_overlap_raises() -> None:
    with pytest.raises(ValueError, match="overlap"):
        _chunk_lines("a\nb", "f.py", 5, -1)


def test_chunk_lines_ids_are_one_based() -> None:
    result = _chunk_lines("a\nb\nc", "app.py", 2, 0)
    # First chunk starts at line 1
    assert result[0][0].startswith("app.py:1-")


def test_chunk_lines_filepath_in_chunk_id() -> None:
    result = _chunk_lines("a\nb", "src/main.py", 2, 0)
    assert result[0][0].startswith("src/main.py:")


# ═══════════════════════════════════════════════════════════════════
# Model registration
# ═══════════════════════════════════════════════════════════════════


def _count_captured(
    captured: list,
    loc_name: str,
    id_substring: str,
) -> int:
    """Count registrations matching location and model id substring."""
    count = 0
    for model, _kwargs in captured:
        mid: str = model.model_id
        if f"fcio-{loc_name}/" in mid and id_substring in mid:
            count += 1
    return count


def _assert_register_called_once(
    captured: list,
    loc_name: str,
    expected_id: str,
    expected_aliases: list[str],
) -> None:
    """Assert exactly one registration for (location, model) with aliases."""
    matches = [
        (model, kwargs)
        for model, kwargs in captured
        if model.model_id == f"fcio-{loc_name}/{expected_id}"
    ]
    assert len(matches) == 1, (
        f"Expected 1 registration for fcio-{loc_name}/{expected_id}, got {len(matches)}"
    )
    _model, kwargs = matches[0]
    assert kwargs.get("aliases") == expected_aliases, (
        f"Expected aliases {expected_aliases}, got {kwargs.get('aliases')}"
    )


_INJECTED_ALIASES: dict[str, list[str]] = {
    "fcio-rzob/gpt-oss-20b": ["gpt-oss-20b", "20b"],
    "fcio-rzob/gpt-oss-120b": ["gpt-oss-120b", "120b"],
    "fcio-rzob/bge-m3-567m": ["bge-m3-567m", "bge"],
    "fcio-rzob/nomic-embed-text-v1_5": ["nomic-embed-text-v1_5", "nomic"],
    "fcio-rzob/embeddinggemma-300m": ["embeddinggemma-300m", "gemma"],
}


def _register_spy(captured: list) -> Callable[..., None]:
    """Factory: create a register callback that appends (model, kwargs) tuples."""

    def _register(model: object, **kwargs: object) -> None:
        captured.append((model, kwargs))

    return _register


@patch("llm_fcio._load_models", return_value=[])
def test_register_models_no_cache_uses_hard_coded_chat(
    _mock_load: object,
) -> None:
    """register_models registriert Chat-Models aus _HARD_CODED_MODELS."""
    captured: list = []
    register_models(_register_spy(captured))

    # 3 locations x 2 chat models = 6 registrations
    assert len(captured) == 6, f"Expected 6 total, got {len(captured)}"

    # Verify structure: all entries are (model, kwargs)
    for model, kwargs in captured:
        assert hasattr(model, "model_id")
        assert isinstance(kwargs, dict)
        assert "aliases" in kwargs

    # Verify rzob has correct models + aliases
    for mid, expected_aliases in _INJECTED_ALIASES.items():
        if "/gpt-oss-" in mid:
            _assert_register_called_once(captured, "rzob", mid.split("/")[1], expected_aliases)

    # Verify dev and whq also got chat models
    for loc in ("dev", "whq"):
        assert _count_captured(captured, loc, "gpt-oss-20b") == 1
        assert _count_captured(captured, loc, "gpt-oss-120b") == 1


@patch("llm_fcio._load_models", return_value=[])
def test_register_embedding_models_no_cache_uses_hard_coded(
    _mock_load: object,
) -> None:
    """register_embedding_models registriert Embedding-Models aus _HARD_CODED."""
    captured: list = []
    register_embedding_models(_register_spy(captured))

    # 3 locations x 3 embedding models = 9 registrations
    assert len(captured) == 9, f"Expected 9 total, got {len(captured)}"

    # Verify rzob has correct models + aliases
    for mid, expected_aliases in _INJECTED_ALIASES.items():
        if "/bge-" in mid or "/nomic-" in mid or "/embeddinggemma-" in mid:
            _assert_register_called_once(captured, "rzob", mid.split("/")[1], expected_aliases)

    # Verify dev and whq
    for loc in ("dev", "whq"):
        for model_id_part in ("bge-m3-567m", "nomic-embed-text-v1_5", "embeddinggemma-300m"):
            assert _count_captured(captured, loc, model_id_part) == 1


@patch("llm_fcio._load_models", return_value=[])
def test_register_models_without_network(
    _mock_load: object,
) -> None:
    """Kein API-Call, kein Cache-File -- Models kommen aus Hard-Code."""
    captured: list = []
    register_models(_register_spy(captured))
    # Nur Struktur prüfen -- kein Netzwerk wurde angerührt
    ids = {m.model_id for m, _k in captured}
    assert "fcio-rzob/gpt-oss-20b" in ids
    assert "fcio-rzob/gpt-oss-120b" in ids
    assert "fcio-dev/gpt-oss-20b" in ids
    assert "fcio-whq/gpt-oss-120b" in ids
