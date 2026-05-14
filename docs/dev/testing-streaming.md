# Verify Streaming Rendering

The plugin renders markdown progressively — blocks appear as they finalize
(blank line or closing code fence), not as one dump at the end. This page
covers how to verify that behavior and debug when it breaks.

## Two Rendering Paths

| Path | Command | Where rendering happens |
|------|---------|------------------------|
| Internal | `llm fcio chat "..."` or `llm fcio simulate` | `_StreamingRenderer` in `llm_fcio.py` |
| Pipe | `llm -m fcio-rzob/... "..." \| uv run scripts/mdscream.py` | `mdscream.py` standalone script |

Both produce identical output. The internal path has no pipe buffering issues.
The pipe path requires `read1()` + `flush()` fixes (see below).

## Quick Smoke Test

```bash
# Internal renderer — blocks appear progressively
llm fcio simulate --speed normal

# Pipe version — same behavior via mdscream.py
llm fcio simulate --speed normal --raw | uv run --no-project scripts/mdscream.py
```

Use `--speed slow` for a more dramatic progressive effect.

## Capture Frames with tmux

tmux provides a real PTY — no ANSI escape pollution, automatable from scripts.

### Manual snapshot

```bash
tmux new-session -s test -x 80 -y 24
# In another terminal:
tmux send-keys -t test 'llm fcio simulate --speed normal 2>/dev/null' Enter
tmux capture-pane -t test -p
```

### Automated frame capture

```bash
tmux new-session -d -s test -x 80 -y 24
tmux send-keys -t test 'llm fcio simulate --speed normal 2>/dev/null' Enter

sleep 1; echo "=== T+1s ==="; tmux capture-pane -t test -p
sleep 2; echo "=== T+3s ==="; tmux capture-pane -t test -p
sleep 5; echo "=== T+8s ==="; tmux capture-pane -t test -p

tmux kill-session -t test
```

Each snapshot should show different content. If only the last snapshot has
output, progressive rendering is broken (everything buffered until process exit).

## Time the Output with `script`

```bash
script -q -f -t -c "llm fcio simulate --speed fast 2>/dev/null" /tmp/output.txt 2>/tmp/timing.txt
```

The timing file contains entries like `0.726 92` (delay in seconds, bytes written).

```bash
# Count timing entries (each = one write burst)
wc -l /tmp/timing.txt

# View delays between writes
awk '{print $1}' /tmp/timing.txt
```

More bursts = more progressive rendering. A single burst means buffering is
broken.

## Trace Syscalls with strace

```bash
strace -f -e trace=write -o /tmp/trace.txt sh -c 'llm fcio simulate --speed fast 2>/dev/null'
grep "write(1" /tmp/trace.txt | head -20
```

Multiple `write(1, ...)` calls spread over time = progressive. One giant write
at the end = buffering.

## Generate SVG Screenshots with Rich

```python
from rich.console import Console
from rich.markdown import Markdown

console = Console(record=True, width=80, force_terminal=True)
console.print(Markdown("# Hello\n\nParagraph with **bold**.\n\n```python\nprint(42)\n```"))
console.save_svg("screenshot.svg", title="llm fcio chat output")
```

## When Progressive Rendering Breaks

Two bugs caused the original "everything appears at once" behavior:

**Input buffering** — `sys.stdin.read()` blocks until EOF. In a streaming pipe,
nothing is read until the upstream process closes. Fix: `sys.stdin.buffer.read1(4096)`
returns immediately with whatever bytes are available.

**Output buffering** — Rich's `Console.print()` does not flush stdout. Fix:
call `sys.stdout.flush()` after every render call.

What does NOT help:

| Approach | Why it fails |
|----------|-------------|
| `PYTHONUNBUFFERED=1` | Fixes output buffering, not input blocking. The process never reaches output code — it's stuck on `stdin.read()`. |
| PTY for stdout | The problem was stdin, not stdout. |
| Python `pty` module | Complex setup, unreliable output capture. |
| `screen` | Requires socket directory that may not exist. |

## Terminal Width in Pipes

`mdscream.py` detects width from stdout (fd 1), not stdin (fd 0), because stdin
is a pipe. The internal renderer does not have this problem — stdout is always
a terminal.
