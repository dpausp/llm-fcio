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

Finalized blocks are rendered with full Rich formatting. The active
(incomplete) block is printed as plain text and replaced with the
formatted version once finalized. No ANSI cursor manipulation needed.
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
    """State machine that splits streaming input into finalized blocks.

    Only the last top-level block can change. We accumulate lines in the
    "active" block. When a block boundary is detected (empty line, closing
    code fence), we finalize it and start a new active block.
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


def _render_block(block: Block) -> None:
    """Render a finalized block with full formatting."""
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
                        background_color="inherit",
                    )
                )
                return
            except Exception:
                pass
        _console.print(Text(code))
        return

    # Text block → Rich Markdown
    text = "\n".join(block.content).strip()
    if not text:
        return
    try:
        _console.print(Markdown(text))
    except Exception:
        _console.print(Text(text))


def _render_active_plain(block: Block) -> None:
    """Print the active block as plain text (will be replaced when finalized)."""
    text = "\n".join(block.content)
    if text.strip():
        sys.stdout.write(text + "\n")
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


def main() -> None:
    detector = BlockDetector()
    buf = ""
    active_lines_printed = 0

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
                # Clear the plain-text active block lines from screen
                if active_lines_printed > 0:
                    # Move up and clear
                    sys.stdout.write(f"\033[{active_lines_printed}A\033[J")
                    sys.stdout.flush()
                    active_lines_printed = 0
                _render_block(block)

        # Print active block as plain text (if new lines arrived)
        active = detector.active
        new_lines = len(active.content) - active_lines_printed
        if new_lines > 0 and active.content:
            for line in active.content[active_lines_printed:]:
                sys.stdout.write(line + "\n")
            sys.stdout.flush()
            active_lines_printed = len(active.content)

    # Feed remaining buffer
    if buf.strip():
        for line in buf.split("\n"):
            finalized = detector.feed_line(line)
            for block in finalized:
                if active_lines_printed > 0:
                    sys.stdout.write(f"\033[{active_lines_printed}A\033[J")
                    sys.stdout.flush()
                    active_lines_printed = 0
                _render_block(block)

    # Flush remaining
    for block in detector.flush():
        if active_lines_printed > 0:
            sys.stdout.write(f"\033[{active_lines_printed}A\033[J")
            sys.stdout.flush()
            active_lines_printed = 0
        _render_block(block)

    # Final active block
    active = detector.active
    if active.content and any(l.strip() for l in active.content):
        if active_lines_printed > 0:
            sys.stdout.write(f"\033[{active_lines_printed}A\033[J")
            sys.stdout.flush()
            active_lines_printed = 0
        _render_block(active)


if __name__ == "__main__":
    main()
