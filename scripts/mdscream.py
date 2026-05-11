#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "rich",
# ]
# ///
"""mdscream — Streaming markdown renderer for terminal pipes.

Reads markdown from stdin (token by token), renders it with Rich formatting
and syntax highlighting as it arrives. Designed to sit in a pipe after LLM
CLIs like `llm`:

    llm -m fcio-rzob "explain decorators" | uv run scripts/mdscream.py

Architecture:
    stdin → Buffer → BlockDetector (state machine) → Renderer (Rich) → stdout

Only the last (active) markdown block is re-rendered on new input.
Finalized blocks are rendered once and never touched again.
"""

import sys
import time
from dataclasses import dataclass, field
from io import StringIO

from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text


# ---------------------------------------------------------------------------
# Block detection
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Block:
    kind: str
    content: list[str] = field(default_factory=list)
    language: str | None = None


class BlockDetector:
    """State machine that splits streaming input into finalized blocks.

    Only the last top-level block can change. Everything before it is
    immutable once a new block boundary is seen. We accumulate lines in
    the "active" block. When we detect that the active block has ended
    (double newline or closing fence), we finalize it and start a new
    active block.
    """

    def __init__(self) -> None:
        self._finalized: list[Block] = []
        self._active = Block(kind="text")
        self._in_code_fence = False

    def feed_line(self, line: str) -> list[Block]:
        """Feed a single line. Returns any newly finalized blocks."""
        newly: list[Block] = []
        stripped = line.rstrip("\r\n")

        # Inside a code fence — only look for closing fence
        if self._in_code_fence:
            if stripped.strip() == "```":
                self._finalized.append(self._active)
                newly.append(self._active)
                self._active = Block(kind="text")
                self._in_code_fence = False
            else:
                self._active.content.append(stripped)
            return newly

        # Code fence opening
        if stripped.startswith("```"):
            # Finalize pending text block if it has content
            if self._active.content and any(
                l.strip() for l in self._active.content
            ):
                self._finalized.append(self._active)
                newly.append(self._active)
            lang = stripped[3:].strip() or None
            self._active = Block(kind="code", language=lang)
            self._in_code_fence = True
            return newly

        # Empty line = paragraph boundary → finalize active block
        if stripped == "" and self._active.content:
            if any(l.strip() for l in self._active.content):
                self._finalized.append(self._active)
                newly.append(self._active)
                self._active = Block(kind="text")
            return newly

        self._active.content.append(stripped)
        return newly

    def flush(self) -> list[Block]:
        """Flush everything remaining, including the active block."""
        newly: list[Block] = []
        if self._active.content and any(
            l.strip() for l in self._active.content
        ):
            self._finalized.append(self._active)
            newly.append(self._active)
            self._active = Block(kind="text")
        return newly

    @property
    def active(self) -> Block:
        return self._active


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

_console = Console(force_terminal=True)
_active_render_height = 0


def _render_block_to_console(con: Console, block: Block) -> None:
    """Render a block to the given console."""
    if block.kind == "code":
        code = "\n".join(block.content)
        if not code.strip():
            return
        lang = block.language
        if lang and lang.lower() not in ("", "text", "plain"):
            try:
                con.print(
                    Syntax(
                        code,
                        lang,
                        theme="monokai",
                        line_numbers=False,
                        word_wrap=False,
                        background_color="default",
                    )
                )
                return
            except Exception:
                pass
        con.print(Text(code))
        return

    # Text block → Rich Markdown
    text = "\n".join(block.content).strip()
    if not text:
        return
    try:
        con.print(Markdown(text))
    except Exception:
        con.print(Text(text))


def _count_rendered_lines(block: Block) -> int:
    """Render block to a capture buffer and count output lines."""
    buf = StringIO()
    con = Console(
        file=buf,
        width=_console.width,
        force_terminal=False,
        no_color=True,
        legacy_windows=False,
    )
    _render_block_to_console(con, block)
    output = buf.getvalue()
    if not output:
        return 0
    # Rich appends newlines; count non-empty trailing segments
    return sum(1 for _ in output.splitlines())


def render_finalized(block: Block) -> None:
    """Render a finalized block (never touched again).

    If the block was already being shown as the active block (cursor is
    right below it), we just mark it as permanent. If it was never
    rendered as active (e.g. a block that was finalized immediately),
    we render it now.
    """
    global _active_render_height

    if _active_render_height == 0:
        # Block was never shown as active — render it now
        _render_block_to_console(_console, block)
        sys.stdout.flush()
    else:
        # Block is already on screen from render_active — just mark permanent
        _active_render_height = 0


def render_active(block: Block) -> None:
    """Render the active (in-progress) block, overwriting previous render.

    Uses ANSI cursor movement to move up and clear the previous rendering
    of this block, then re-renders it. This is the anti-flicker mechanism.
    """
    global _active_render_height

    if _active_render_height > 0:
        sys.stdout.write(f"\033[{_active_render_height}A\033[J")
        sys.stdout.flush()

    _active_render_height = _count_rendered_lines(block)
    _render_block_to_console(_console, block)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_cr(text: str) -> str:
    """Keep only text after the last carriage return per line."""
    if "\r" not in text:
        return text
    lines = text.split("\n")
    result: list[str] = []
    for line in lines:
        if "\r" in line:
            line = line.split("\r")[-1]
        result.append(line)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

READ_SIZE = 4096
DEBOUNCE_S = 0.05  # 50ms between active-block re-renders


def main() -> None:
    detector = BlockDetector()
    buf = ""
    last_active_render = 0.0

    while True:
        chunk = sys.stdin.read(READ_SIZE)
        if not chunk:
            break

        chunk = _normalize_cr(chunk)
        buf += chunk

        # Process complete lines
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            finalized = detector.feed_line(line)

            for block in finalized:
                render_finalized(block)

        # Re-render active block (debounced)
        active = detector.active
        if active.content and any(l.strip() for l in active.content):
            now = time.monotonic()
            if now - last_active_render >= DEBOUNCE_S:
                render_active(active)
                last_active_render = now

    # Feed remaining buffer
    if buf.strip():
        for line in buf.split("\n"):
            finalized = detector.feed_line(line)
            for block in finalized:
                render_finalized(block)

    # Flush remaining
    for block in detector.flush():
        render_finalized(block)

    # Final active block
    active = detector.active
    if active.content and any(l.strip() for l in active.content):
        render_finalized(active)


if __name__ == "__main__":
    main()
