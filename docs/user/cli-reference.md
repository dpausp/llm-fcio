# CLI Reference

All commands live under `llm fcio`. Select a location with `-l`/`--location` (default: `rzob`).

Prerequisites: `llm` installed, API key set via `llm keys set fcio-rzob` or the `FCIO_RZOB_API_KEY` environment variable.

---

## llm fcio refresh

Fetches models from the API and caches them locally. Most other commands depend on this cache.

```bash
llm fcio refresh
```

Run after first install and when new models appear. The cache lives at `~/.llm/fcio_models_{location}.json` and persists until you refresh again.

---

## llm fcio models

Lists all available models with their type (chat or embed).

```bash
# List everything
llm fcio models

# Filter by name substring
llm fcio models --filter oss

# Raw JSON output
llm fcio models --json
```

`--filter <substring>`
: Shows only models whose ID contains the substring (case-insensitive).

`--json`
: Dumps the raw API response as JSON.

---

## llm fcio chat

Sends a prompt to a chat model with optional Rich markdown rendering. Supports one-shot, interactive, streaming, and system prompts.

```bash
# One-shot (default model: gpt-oss:20b)
llm fcio chat "What is 2+2?"

# Specific model
llm fcio chat -m 120b "Explain DNS"

# System prompt
llm fcio chat -s "You are a terse technical assistant" "Explain DNS"

# Interactive conversation
llm fcio chat -i

# Non-streaming with JSON output
llm fcio chat --no-stream --json "Hello"
```

**Model name resolution:** Pass a substring instead of the full ID. With [fzf](https://github.com/junegunn/fzf) installed, ambiguous matches open an interactive picker. Without fzf, unambiguous substrings still resolve; ambiguous ones produce an error.

`-m, --model <id>`
: Selects the model. Accepts fuzzy names (default: `gpt-oss:20b`).

`-s, --system <prompt>`
: Sets a system prompt that shapes the model's behavior for the entire conversation.

`-t, --temperature <float>`
: Controls output randomness (default: 0.7). Higher values produce more varied responses.

`--max-tokens <int>`
: Caps the response at this many tokens.

`--stream / --no-stream`
: Streams the response token by token, or waits for the full response before printing (default: stream).

`--markdown / --no-markdown`
: Renders markdown with Rich formatting (syntax highlighting, headings, code blocks). Auto-detects terminal vs pipe. Use `--no-markdown` for raw output in a terminal, `--markdown` to force rendering when piping.

`--json`
: Outputs the full API response as JSON instead of rendered text.

`-i, --interactive`
: Starts a multi-turn conversation. Press Ctrl+D to exit.

**Failure modes:** Without a prompt and without `--interactive`, the command fails. If the model name matches multiple models and fzf is not installed, you get an ambiguity error listing the matches.

---

## llm fcio embed

Generates embeddings for one or more texts. For bulk ingestion, use [ingest](#llm-fcio-ingest) instead.

```bash
# Single text
llm fcio embed bge "Hello world"

# Multiple texts
llm fcio embed bge "first text" "second text"

# Raw JSON with usage data
llm fcio embed bge "Hello" --json
```

The first argument is the model ID or alias. Run `llm fcio models --filter embed` to see available embedding models.

`-m, --model <id>`
: Selects the embedding model (default: `bge-m3-567m`).

`--json`
: Outputs raw JSON including usage data.

`-d, --dimensions <int>`
: Truncates the embedding vector to this many dimensions.

---

## llm fcio ingest

Chunks files into line-based overlapping segments, embeds them, and stores them in a named collection for `llm similar` queries.

```bash
# Ingest markdown files from a directory (recursive)
llm fcio ingest my-project .

# Python files with custom chunking
llm fcio ingest my-project ./src/ --glob "*.py" --chunk-size 50 --overlap 10

# Explicit files
llm fcio ingest my-project README.md CONTRIBUTING.md --chunk-size 50

# Skip the confirmation preview
llm fcio ingest my-project ./docs/ --yes
```

### How chunking works

Files are split into segments of `--chunk-size` lines. Consecutive chunks overlap by `--overlap` lines so information at chunk boundaries is preserved. Each chunk ID encodes the file path and line range (e.g. `src/main.py:42-71`) for tracing similarity results back to the source.

### File discovery

When you pass a directory, files are found recursively using the `--glob` pattern. Files matching `.gitignore` rules are excluded. These directories are always excluded: `venv/`, `.venv/`, `node_modules/`, `__pycache__/`, `.git/`, `*.egg-info/`.

Explicit file paths bypass all filtering — they are chunked and embedded as-is.

### Confirmation preview

By default, the command lists all discovered files with their chunk counts and asks for confirmation. Use `--yes` to skip this.

### Ingesting multiple file types

Multiple invocations write into the same collection:

```bash
llm fcio ingest my-project . --glob "*.md" --yes
llm fcio ingest my-project . --glob "*.py" --chunk-size 50 --overlap 10 --yes

# Search across both
llm similar my-project -c "how does error handling work"
```

Manage collections with standard `llm` commands:

```bash
llm collections list
llm collections delete my-project
```

### Options

`collection`
: Name for the embedding collection (required, first positional argument).

`paths`
: One or more directories or file paths (required).

`--glob <pattern>`
: File pattern for directory discovery (default: `*.md`).

`-m, --model <id>`
: Embedding model for a new collection (default: `bge-m3-567m`). Ignored if the collection already exists — delete and recreate to change models.

`--chunk-size <int>`
: Lines per chunk (default: 30).

`--overlap <int>`
: Overlap lines between consecutive chunks (default: 5).

`--yes`
: Skips the confirmation preview and starts embedding immediately.

### Failure modes

- **No files found** — the command fails. Check your `--glob` pattern and directory path.
- **Empty files** — produce no chunks and are silently skipped.
- **Existing collection** — the `-m` flag is ignored. Delete and recreate the collection to change models.

---

## llm fcio health

Checks API connectivity, authentication, and endpoint availability.

```bash
llm fcio health
```

Reports three checks:

- **auth** — whether your API key is valid (tested by fetching the model list)
- **base_url** — whether the API server is reachable
- **chat_endpoint** — whether the chat completions endpoint responds

Use this as a first step when troubleshooting.

`--json`
: Outputs results as JSON.

---

## llm fcio capabilities

Shows detailed endpoint capabilities, available models by type (chat/embed/other), and feature probes (chat completions, streaming, embeddings).

```bash
llm fcio capabilities

# Raw JSON output
llm fcio capabilities --json
```

`--json`
: Outputs full capability report as JSON.

---

## llm fcio tokens

Estimates how many tokens a prompt consumes by sending a minimal request and reporting usage from the API response.

```bash
llm fcio tokens gpt-oss:20b "count these tokens"

# JSON output
llm fcio tokens gpt-oss:20b "some text" --json
```

If the API does not return token usage, falls back to a rough heuristic (~4 characters per token) and prints a warning.

`-m, --model <id>`
: Selects the model to test against (default: `gpt-oss:20b`).

`text`
: One or more words forming the prompt (required).

`--json`
: Outputs usage data as JSON.

---

## llm fcio simulate

Streams a simulated LLM response with Rich markdown rendering. Useful for testing the rendering pipeline without hitting the API.

```bash
llm fcio simulate

# Raw markdown output (no Rich formatting)
llm fcio simulate --raw

# Adjust streaming speed
llm fcio simulate --speed fast
```

`--speed <speed>`
: Streaming speed: `fast`, `normal`, or `slow` (default: `normal`).

`--seed <int>`
: Random seed for reproducible output (default: 42).

`--raw`
: Outputs raw markdown without Rich formatting.
