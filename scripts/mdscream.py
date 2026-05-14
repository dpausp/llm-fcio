#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "rich",
# ]
# ///
"""mdscream — Streaming markdown renderer for terminal pipes.

Reads markdown from stdin (token by token), renders it with Rich formatting
and syntax highlighting. Designed to sit in a pipe after LLM CLIs:

    llm -m fcio-rzob "explain decorators" | uv run scripts/mdscream.py

Finalized blocks (delimited by blank lines or closed code fences) are
rendered with full Rich formatting. The active (incomplete) block is
buffered until finalized. No ANSI cursor manipulation — just clean output.
"""

import sys
from dataclasses import dataclass, field

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
    """Split streaming input into finalized blocks.

    Accumulates lines in an "active" block. When a block boundary is
    detected (empty line or closing code fence), finalizes the block.
    """

    def __init__(self) -> None:
        self._active = Block(kind="text")
        self._in_code_fence = False

    def feed_line(self, line: str) -> list[Block]:
        """Feed a single line. Returns any newly finalized blocks."""
        newly: list[Block] = []
        stripped = line.rstrip("\r\n")

        if self._in_code_fence:
            if stripped.strip() == "```":
                newly.append(self._active)
                self._active = Block(kind="text")
                self._in_code_fence = False
            else:
                self._active.content.append(stripped)
            return newly

        if stripped.startswith("```"):
            if self._active.content and any(
                ln.strip() for ln in self._active.content
            ):
                newly.append(self._active)
            lang = stripped[3:].strip() or None
            self._active = Block(kind="code", language=lang)
            self._in_code_fence = True
            return newly

        if stripped == "" and self._active.content:
            if any(ln.strip() for ln in self._active.content):
                newly.append(self._active)
                self._active = Block(kind="text")
            return newly

        self._active.content.append(stripped)
        return newly

    def flush(self) -> list[Block]:
        """Flush the active block if it has content."""
        if self._active.content and any(
            ln.strip() for ln in self._active.content
        ):
            block = self._active
            self._active = Block(kind="text")
            return [block]
        return []


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def _detect_width() -> int:
    """Detect terminal width from stdout/stderr (not stdin, which is piped)."""
    import os
    for fd in (1, 2, 0):
        try:
            return os.get_terminal_size(fd).columns
        except OSError:
            continue
    return 80


_console = Console(
    force_terminal=True,
    width=_detect_width(),
)


def _render_block(block: Block) -> None:
    """Render a finalized block with full Rich formatting."""
    if block.kind == "code":
        code = "\n".join(block.content)
        if not code.strip():
            return
        lang = block.language
        if lang and lang.lower() not in ("", "text", "plain"):
            try:
                _console.print(
                    Syntax(
                        code,
                        lang,
                        theme="monokai",
                        line_numbers=False,
                        word_wrap=False,
                    )
                )
                sys.stdout.flush()
                return
            except (ValueError, OSError):
                pass
        _console.print(Text(code))
        sys.stdout.flush()
        return

    text = "\n".join(block.content).strip()
    if not text:
        return
    try:
        _console.print(Markdown(text))
    except (ValueError, OSError):
        _console.print(Text(text))
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_cr(text: str) -> str:
    """Keep only text after the last carriage return per line."""
    if "\r" not in text:
        return text
    return "\n".join(
        line.split("\r")[-1] if "\r" in line else line
        for line in text.split("\n")
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

READ_SIZE = 4096


def main() -> None:
    detector = BlockDetector()
    buf = ""

    while True:
        raw = sys.stdin.buffer.read1(READ_SIZE)
        if not raw:
            break
        chunk = raw.decode("utf-8", errors="replace")

        chunk = _normalize_cr(chunk)
        buf += chunk

        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            for block in detector.feed_line(line):
                _render_block(block)

    # Remaining buffer
    if buf.strip():
        for line in buf.split("\n"):
            for block in detector.feed_line(line):
                _render_block(block)

    # Flush active block
    for block in detector.flush():
        _render_block(block)


if __name__ == "__main__":
    main()
