# Testing Streaming Output

The plugin renders markdown progressively — blocks appear as they finalize (blank line or closing code fence), not as one dump at the end. This page documents how to verify that behavior and debug issues.

## Two Rendering Paths

| Path | Command | Where rendering happens |
|------|---------|------------------------|
| Internal | `llm rzob chat "..."` or `llm rzob simulate` | `_StreamingRenderer` in `llm_fcio.py` |
| Pipe | `llm -m fcio-rzob/... "..." \| uv run scripts/mdscream.py` | `mdscream.py` standalone script |

Both produce identical output. The internal path has no pipe buffering issues. The pipe path requires `read1()` + `flush()` fixes (see below).

## Quick Smoke Test

```bash
# Internal renderer — should show blocks appearing progressively
llm rzob simulate --speed normal

# Pipe version — same behavior via mdscream.py
llm rzob simulate --speed normal --raw | uv run --no-project scripts/mdscream.py
```

Use `--speed slow` for a more dramatic progressive effect.

## Testing with tmux

tmux provides a real terminal environment (PTY) and can be scripted for automated snapshot testing.

### Manual testing

```bash
# Create a session
tmux new-session -s test -x 80 -y 24

# In another terminal, run the command
tmux send-keys -t test 'llm rzob simulate --speed normal 2>/dev/null' Enter

# Capture the current pane state
tmux capture-pane -t test -p
```

### Automated frame capture

Capture snapshots at intervals to verify progressive rendering:

```bash
tmux new-session -d -s test -x 80 -y 24
tmux send-keys -t test 'llm rzob simulate --speed normal 2>/dev/null' Enter

sleep 1; echo "=== T+1s ==="; tmux capture-pane -t test -p
sleep 2; echo "=== T+3s ==="; tmux capture-pane -t test -p
sleep 5; echo "=== T+8s ==="; tmux capture-pane -t test -p

tmux kill-session -t test
```

If progressive rendering works, each snapshot shows different content. If broken, only the last snapshot has output (everything appeared at once when the process exited).

### Why tmux works

- `send-keys` injects commands into a real PTY
- `capture-pane -p` reads the terminal buffer directly
- No ANSI escape pollution in captures
- `new-session -d` runs detached (automatable from scripts)

## Timing Analysis with `script`

The `script` command records terminal output with precise timing data:

```bash
script -q -f -t -c "llm rzob simulate --speed fast 2>/dev/null" /tmp/output.txt 2>/tmp/timing.txt
```

The timing file (`-t`) contains entries like `0.726 92` (delay in seconds, bytes written). Parse to count bursts:

```bash
# Count timing entries (each = one write burst)
wc -l /tmp/timing.txt

# View the delays between writes
awk '{print $1}' /tmp/timing.txt
```

More bursts = more progressive rendering. A single burst means buffering is broken.

## Generating Screenshots with Rich SVG

The `Rich` library can export rendered output as SVG for documentation:

```python
from rich.console import Console
from rich.markdown import Markdown

console = Console(record=True, width=80, force_terminal=True)
console.print(Markdown("# Hello\n\nParagraph with **bold**.\n\n```python\nprint(42)\n```"))
console.save_svg("screenshot.svg", title="llm rzob chat output")
```

This is how `chat-screenshot.svg` in the repo root was generated.

## Syscall Debugging with strace

When investigating buffering issues, `strace` shows exactly when `write()` syscalls happen:

```bash
strace -f -e trace=write -o /tmp/trace.txt sh -c 'llm rzob simulate --speed fast 2>/dev/null'
grep "write(1" /tmp/trace.txt | head -20
```

Multiple `write(1, ...)` calls spread over time = progressive. One giant write at the end = buffering.

## Buffering Root Causes

Two bugs caused the original "everything appears at once" behavior:

### Input buffering: `sys.stdin.read()` blocks until EOF

`mdscream.py` used `sys.stdin.read(4096)` which blocks until 4096 bytes are available **or** EOF. In a streaming pipe, this means nothing is read until the upstream process closes.

**Fix:** `sys.stdin.buffer.read1(4096)` returns immediately with whatever bytes are available.

### Output buffering: missing `sys.stdout.flush()`

Rich's `Console.print()` does not flush stdout. In a pipe or PTY, output accumulates in Python's buffer.

**Fix:** Call `sys.stdout.flush()` after every `Console.print()` in `_render()`.

### What does NOT help

| Approach | Why it fails |
|----------|-------------|
| `PYTHONUNBUFFERED=1` | Fixes **output** buffering, not **input** blocking. The process never reaches output code because it's stuck on `stdin.read()`. |
| PTY for stdout | The problem was stdin, not stdout. Allocating a PTY for the output side doesn't help when the input side is blocked. |
| Python `pty` module | Complex setup, unreliable output capture from the master fd. |
| `screen` | Requires socket directory that may not exist. |
| `zellij` | Requires interactive terminal, not automatable from scripts. |

## Terminal Width Detection in Pipes

When `mdscream.py` receives piped input, `shutil.get_terminal_size()` checks stdin (fd 0) first — which is a pipe, not a terminal. It falls back to a hardcoded default (120 columns), which garbles output on narrower terminals.

The fix detects width from **stdout** (fd 1), which IS the terminal:

```python
def _detect_width() -> int:
    for fd in (1, 2, 0):  # stdout, stderr, stdin
        try:
            return os.get_terminal_size(fd).columns
        except OSError:
            continue
    return 80
```

The internal renderer (`_StreamingRenderer`) does not have this problem because stdout is always a terminal.
