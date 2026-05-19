"""Tests for _StreamingRenderer edge cases covering missing lines.

Each test targets specific uncovered lines in the _StreamingRenderer class
(llm_fcio.py lines ~469-569). Docstrings note the exact line coverage.
"""

from typing import TYPE_CHECKING
from unittest.mock import patch

from llm_fcio import _Block, _StreamingRenderer

if TYPE_CHECKING:
    import pytest

# ── flush() with content in buffer ──────────────────────────────


def test_flush_processes_remaining_buffer_content() -> None:
    """Lines 493-496: flush() detects + renders remaining buffer content.

    Feed content without a trailing newline so it stays in _buf,
    then flush() must process it and clear the buffer.
    """
    renderer = _StreamingRenderer()
    renderer.feed("some text")
    assert renderer._buf.strip()
    renderer.flush()
    assert renderer._buf == ""


def test_flush_renders_blocks_from_detect() -> None:
    """Line 495: flush() calls _render for blocks returned by _detect().

    Feed text then a code fence opener without newline. The code fence
    stays in the buffer. flush() calls _detect() which finalizes the active
    text block (because a code fence opens), returning it for rendering.
    """
    renderer = _StreamingRenderer()
    renderer.feed("hello\n")
    assert renderer._active.content  # active has text
    renderer.feed("```python")  # stays in buffer (no newline)
    assert renderer._buf == "```python"
    renderer.flush()
    assert renderer._buf == ""
    # The text block was rendered, active is now the code block
    assert renderer._active.kind == "code"


# ── _finalize_active() returns empty list ───────────────────────


def test_finalize_active_returns_empty_when_no_content() -> None:
    """Line 537: _finalize_active() returns [] when active block is empty.

    Calling flush() on a fresh renderer (no content fed) hits this path.
    """
    renderer = _StreamingRenderer()
    result = renderer._finalize_active()
    assert result == []


def test_flush_on_empty_renderer_is_noop() -> None:
    """Line 537: flush() on a renderer with no pending content.

    The active block is empty, so _finalize_active returns [].
    """
    renderer = _StreamingRenderer()
    renderer.flush()
    assert renderer._buf == ""
    assert renderer._active.content == []


# ── Code fence opens while active text block has content ────────


def test_code_fence_finalizes_active_text_block() -> None:
    """Line 517: opening a code fence finalizes the pending text block.

    Feed text lines, then a code fence — the text block should be emitted.
    """
    renderer = _StreamingRenderer()
    # Feed a text line (with newline so it's processed)
    renderer.feed("hello world\n")
    # Active block now has content
    assert renderer._active.content
    # Now open a code fence — this should finalize the text block
    renderer.feed("```python\n")
    # After the code fence, active should be a new code block
    assert renderer._active.kind == "code"
    assert renderer._active.language == "python"


# ── Code block with empty/whitespace-only content ───────────────


def test_empty_code_block_not_rendered() -> None:
    """Lines 544-545: code block with empty content is skipped.

    Feed a code fence opening then immediately a closing fence.
    The code block has no content lines, so _render returns early.
    """
    renderer = _StreamingRenderer()
    # Open and close code fence with nothing inside
    renderer.feed("```python\n```\n")
    # The empty code block should have been silently skipped
    # No exception and active block should be text again
    assert renderer._active.kind == "text"


def test_whitespace_only_code_block_not_rendered() -> None:
    """Lines 544-545: code block with only whitespace content is skipped."""
    renderer = _StreamingRenderer()
    renderer.feed("```python\n   \n\t\n```\n")
    assert renderer._active.kind == "text"


# ── Syntax highlighting exception fallback ──────────────────────


def test_syntax_exception_falls_back_to_plain_text(capsys: pytest.CaptureFixture[str]) -> None:
    """Lines 554-555: Syntax() raising falls back to plain Text().

    Patch rich.syntax.Syntax to raise, feed a code block with a language
    that would normally trigger syntax highlighting.
    """
    renderer = _StreamingRenderer()
    with patch("llm_fcio.Syntax", side_effect=RuntimeError("bad lexer")):
        renderer.feed("```python\nprint('hello')\n```\n")

    captured = capsys.readouterr()
    # Plain text fallback should still output the code
    assert "print" in captured.out


# ── Empty text block in render ──────────────────────────────────


def test_text_block_with_only_whitespace_not_rendered() -> None:
    """Lines 561-562: text block with all-whitespace lines is skipped.

    Build a block with whitespace-only content and render it directly.
    """
    renderer = _StreamingRenderer()
    block = _Block(kind="text", content=["   ", "\t", "  "])
    # Should return without printing anything (no exception)
    renderer._render(block)


def test_text_block_with_empty_content_not_rendered() -> None:
    """Lines 561-562: text block with empty content list is skipped."""
    renderer = _StreamingRenderer()
    block = _Block(kind="text", content=[])
    renderer._render(block)


# ── Markdown rendering exception fallback ───────────────────────


def test_markdown_exception_falls_back_to_plain_text(capsys: pytest.CaptureFixture[str]) -> None:
    """Lines 566-568: Markdown() raising falls back to plain Text().

    Patch rich.markdown.Markdown to raise, then feed text content.
    """
    renderer = _StreamingRenderer()
    with patch("llm_fcio.Markdown", side_effect=RuntimeError("bad markdown")):
        renderer.feed("Hello **world**\n\n")

    captured = capsys.readouterr()
    # Fallback plain text should contain the content
    assert "Hello" in captured.out


# ── Combination / integration ───────────────────────────────────


def test_flush_after_complete_feed_is_noop() -> None:
    """Flush after all content already processed (buffer empty).

    Feed content with trailing newlines (fully processed), then flush.
    The active block is empty after the blank-line boundary, so
    _finalize_active returns [].
    """
    renderer = _StreamingRenderer()
    renderer.feed("hello\n\n")
    # Blank line finalizes the text block; active is now empty
    assert renderer._active.content == []
    assert renderer._buf == ""
    renderer.flush()
    assert renderer._buf == ""


def test_feed_code_with_language_then_close() -> None:
    """Full code block lifecycle: open with language, content, close."""
    renderer = _StreamingRenderer()
    renderer.feed("```python\nx = 1\ny = 2\n```\n")
    assert renderer._active.kind == "text"
    assert renderer._active.content == []
