# CLI Reference

All commands are under `llm rzob`. Prerequisites: `llm` installed, API key set via `llm keys set fcio-rzob` or the `FCIO_RZOB_API_KEY` environment variable.

---

## llm rzob refresh

Fetch available models from the API and cache them locally. Model registration and most other commands depend on this cache.

```bash
llm rzob refresh
```

Run after first install and when new models may have been added to the API. The cache lives in your llm user directory and persists until you refresh again.

---

## llm rzob models

List all models available on the API with their type (chat or embed).

```bash
# List everything
llm rzob models

# Filter by name substring
llm rzob models --filter oss

# Raw JSON output
llm rzob models --json
```

`--filter <substring>`
: Show only models whose ID contains the substring (case-insensitive).

`--json`
: Output raw JSON from the API.

---

## llm rzob chat

Send a prompt to a chat model. Supports one-shot, interactive, streaming, and system prompts.

```bash
# One-shot (default model: gpt-oss:20b)
llm rzob chat "What is 2+2?"

# Specific model
llm rzob chat -m 120b "Explain DNS"

# System prompt
llm rzob chat -s "You are a terse technical assistant" "Explain DNS"

# Interactive conversation
llm rzob chat -i

# Non-streaming with JSON output
llm rzob chat --no-stream --json "Hello"
```

**Model name resolution:** Pass a substring instead of the full ID. If you have [fzf](https://github.com/junegunn/fzf) installed, ambiguous matches open an interactive picker. Without fzf, unambiguous substrings still resolve. Ambiguous substrings without fzf produce an error.

`-m, --model <id>`
: Model to use. Accepts fuzzy names (default: `gpt-oss:20b`).

`-s, --system <prompt>`
: Set a system prompt that shapes the model's behavior.

`-t, --temperature <float>`
: Sampling temperature (default: 0.7). Higher values produce more varied output.

`--max-tokens <int>`
: Cap the response length in tokens.

`--stream / --no-stream`
: Stream the response token by token, or wait for the full response (default: stream).

`--json`
: Output the full API response as JSON instead of plain text.

`-i, --interactive`
: Start a multi-turn conversation. Press Ctrl+D to exit.

**When things go wrong:** Without a prompt and without `--interactive`, the command fails with an error. If the model name matches multiple models and fzf is not installed, you get an ambiguity error listing the matches.

---

## llm rzob embed

Generate embeddings for one or more texts. Use this for testing — for bulk ingestion, see [ingest](#llm-rzob-ingest).

```bash
# Single text
llm rzob embed bge "Hello world"

# Multiple texts
llm rzob embed bge "first text" "second text"

# Raw JSON with usage data
llm rzob embed bge "Hello" --json
```

The first argument is the model ID or alias. Run `llm rzob models --filter embed` to see what's available.

`--json`
: Output raw JSON including usage data.

`-d, --dimensions <int>`
: Truncate the embedding vector to a specific number of dimensions.

---

## llm rzob ingest

Chunk files into line-based overlapping segments, embed them, and store them in a named collection for semantic search via `llm similar`.

```bash
# Ingest markdown files from a directory (recursive)
llm rzob ingest my-project .

# Python files with custom chunking
llm rzob ingest my-project ./src/ --glob "*.py" --chunk-size 50 --overlap 10

# Explicit files
llm rzob ingest my-project README.md CONTRIBUTING.md --chunk-size 50

# Skip the confirmation preview
llm rzob ingest my-project ./docs/ --yes
```

### How chunking works

Files are split into segments of `--chunk-size` lines. Consecutive chunks overlap by `--overlap` lines so that information at chunk boundaries is not lost. Each chunk gets an ID encoding the file path and line range (e.g. `src/main.py:42-71`) so you can trace similarity results back to the source.

### File discovery

When you pass a directory, files are found recursively using the `--glob` pattern. Files matching `.gitignore` rules are excluded. Common directories (`venv/`, `.venv/`, `node_modules/`, `__pycache__/`, `.git/`) are always excluded.

When you pass explicit file paths, no filtering is applied — the files are chunked and embedded as-is.

### Confirmation preview

By default, the command lists all discovered files with their chunk counts and asks for confirmation before embedding. Use `--yes` to skip this.

### Ingesting multiple file types

Multiple invocations write into the same collection. Ingest different formats separately:

```bash
llm rzob ingest my-project . --glob "*.md" --yes
llm rzob ingest my-project . --glob "*.py" --chunk-size 50 --overlap 10 --yes

# Search across both
llm similar my-project -c "how does error handling work"
```

Manage collections with the standard `llm` commands:

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
: Embedding model (default: `bge-m3-567m`). Only applies when creating a new collection.

`--chunk-size <int>`
: Lines per chunk (default: 30).

`--overlap <int>`
: Overlap lines between consecutive chunks (default: 5).

`--yes`
: Skip the confirmation preview.

### When things go wrong

- **No files found** — the command fails. Check your `--glob` pattern and directory path.
- **Empty files** — produce no chunks and are silently skipped.
- **Existing collection** — the `-m` flag is ignored. The collection keeps its original model. To change models, delete and recreate the collection.

---

## llm rzob health

Check API connectivity, authentication, and endpoint availability.

```bash
llm rzob health
```

Reports three checks:

- **auth** — whether your API key is valid (tested by fetching the model list)
- **base_url** — whether the API server is reachable
- **chat_endpoint** — whether the chat completions endpoint responds

Use this as a first step when troubleshooting.

`--json`
: Output results as JSON.

---

## llm rzob tokens

Estimate how many tokens a prompt consumes. Sends a minimal request and reports counts from the API response.

```bash
llm rzob tokens gpt-oss:20b "count these tokens"

# JSON output
llm rzob tokens gpt-oss:20b "some text" --json
```

If the API does not return token usage, the command falls back to a rough heuristic (~4 characters per token) and prints a warning.

`model_id`
: The model to test against (required, first positional argument).

`text`
: One or more words forming the prompt (required).

`--json`
: Output usage data as JSON.
