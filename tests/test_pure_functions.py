"""Tests for pure functions in llm_fcio.

Covers _chunk_lines, _discover_files, and _build_chat_body with edge cases.
"""

from pathlib import Path

import click
import pytest

from llm_fcio import _build_chat_body, _chunk_lines, _discover_files

# ── _chunk_lines ────────────────────────────────────────────────


def test_chunk_lines_empty_string() -> None:
    assert _chunk_lines("", "f.py", 10, 2) == []


def test_chunk_lines_only_newlines() -> None:
    # splitlines() on "\n\n\n" produces ['','',''] — not empty
    result = _chunk_lines("\n\n\n", "f.py", 10, 2)
    assert len(result) == 1
    assert result[0] == ("f.py:1-3", "\n\n")


def test_chunk_lines_one_line() -> None:
    result = _chunk_lines("hello", "f.py", 10, 2)
    assert len(result) == 1
    assert result[0] == ("f.py:1-1", "hello")


def test_chunk_lines_one_line_trailing_newline() -> None:
    result = _chunk_lines("hello\n", "f.py", 10, 2)
    # splitlines() ignores trailing newline — single line
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
    # chunk_size=5, overlap=2 → step=3
    result = _chunk_lines(text, "f.py", 5, 2)
    assert len(result) == 3
    assert result[0][0] == "f.py:1-5"
    assert result[1][0] == "f.py:4-8"
    assert result[2][0] == "f.py:7-10"


def test_chunk_lines_overlap_content_shared() -> None:
    text = "\n".join(f"line{i}" for i in range(7))
    # chunk_size=4, overlap=1 → step=3
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


# ── _discover_files ──────────────────────────────────────────────


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
    # Passing a single file with a glob it doesn't match
    # Note: when a file path is passed directly, it's always included
    # regardless of glob — the function checks is_file() first
    result = _discover_files((f,), "*.md")
    # Single files are included directly without glob filtering
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
    # *.log is gitignored, so debug.log is excluded
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


# ── _build_chat_body ─────────────────────────────────────────────


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
