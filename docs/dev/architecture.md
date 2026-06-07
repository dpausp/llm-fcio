# Architecture

llm-fcio is a single-file `llm` CLI plugin (`llm_fcio.py`) that connects to
OpenAI-compatible API endpoints at multiple FCIO locations. It registers chat
models, embedding models, and CLI commands through `llm`'s plugin hook system.

## Why a Single File

The entire plugin lives in `llm_fcio.py` — no `src/` layout, no submodules.
This is intentional: the plugin has a flat dependency graph (httpx, click, Rich,
pathspec, sqlite-utils) and no internal module boundaries worth enforcing.
When the plugin grows complex enough to warrant multiple modules, that's the
right time to split — not before.

## Multi-Location Design

The plugin talks to three FCIO API locations, each with its own base URL and
key:

| Location | API Base | Key name |
|----------|----------|----------|
| `rzob` (default) | `ai.rzob.fcio.net/openai/v1` | `fcio-rzob` |
| `dev` | `ai.dev.fcio.net/openai/v1` | `fcio-dev` |
| `whq` | `ai.whq.fcio.net/openai/v1` | `fcio-whq` |

All commands live under `llm fcio` with a `--location` flag (default: `rzob`).
Each location gets its own model cache file (`~/.llm/fcio_models_{location}.json`)
and resolves its API key independently.

The `Location` dataclass carries `name`, `api_base`, `key_name`, and `env_var`
so every function that needs API access takes a `Location` instead of individual
strings. This avoids the previous design where `API_BASE` and `KEY_NAME` were
module-level constants — adding a new location now means one entry in the
`LOCATIONS` dict.

## Model Cache Bridge

Models are not hardcoded. The plugin discovers them at runtime:

1. `llm fcio refresh` fetches `/models` from the API and writes a JSON cache.
2. On plugin load, `register_models` and `register_embedding_models` iterate
   the cache and register each model with `llm`.

This two-step design exists because `llm`'s hook system requires all models
registered at import time — you can't make API calls during registration. The
cache bridges this gap. Without a cache (no `refresh` run yet), the plugin
registers nothing and stays inert.

The cache stores dicts with `id` and `safe_id` keys. If the cache is missing or
corrupt, `llm fcio refresh` regenerates it from the API.

### Safe ID Mapping

API model IDs contain characters illegal in `llm` model names (colons, dots).
The plugin generates a `safe_id` by replacing `:` → `-` and `.` → `_`. The
`llm` model ID is always `fcio-{location}/safe_id`, while the API call uses
the original ID. This mapping happens at cache time, not at call time.

### Embedding Detection

There is no API flag distinguishing chat from embedding models. The plugin
uses a keyword heuristic: model IDs containing `embed`, `bge`, or `gemma`
register as `RzobEmbeddingModel`, everything else as `RzobModel`. This is
fragile — a new embedding model without these keywords in its ID won't be
detected. The trade-off: no extra API call, no config file, works for the
known model set.

## API Communication

All HTTP calls go through `httpx` with Bearer token auth. Key resolution:
`llm` key store → `FCIO_{LOCATION}_API_KEY` env var → error.

### SSE Streaming

Chat completions support SSE streaming via `httpx-sse`. The core is
`_iter_sse_content(client, url, headers, body) -> tuple[_SSEMetadata, Iterator[str]]` — a shared
generator that handles connection, iteration, JSON parsing, and delta
extraction. It returns a metadata accumulator (`finish_reason`, `usage`)
alongside the content iterator. Both `RzobModel.execute()` and the `chat`
CLI command delegate to this generator, eliminating duplicated SSE loops.
The generator skips `data: [DONE]` termination and yields content deltas
incrementally. Metadata is populated during iteration — read it after the
iterator is consumed.

Non-streaming calls hit the same endpoint without the `stream` parameter.

### Error Handling

Two custom exception classes cover programmatic errors:

- `ModelError` — ambiguous or unknown model resolution
- `ApiError` — API communication failures (status code, empty response,
  streaming errors)

CLI-facing validation (missing prompt, path not found, no files, aborted)
uses `click.ClickException` — these are UX errors, not programmatic ones.

HTTP status checks use `httpx.codes` named constants (`BAD_REQUEST`,
`UNAUTHORIZED`, `NOT_FOUND`) with `>= BAD_REQUEST` threshold, not raw integers.

## Streaming Markdown Renderer

`_StreamingRenderer` renders markdown progressively during streaming chat.
Instead of dumping the entire response at once, it renders each block (paragraph,
code fence, list) as it finalizes.

The design: input streams as arbitrary token-sized chunks. A line buffer splits
on `\n` boundaries. A state machine detects block boundaries (blank line →
finalize text block, code fence → start/end code block). Each finalized block
renders immediately via Rich (`Markdown` for text, `Syntax` for code with known
language, plain `Text` for untagged code). Every render call flushes stdout to
guarantee terminal output without buffering.

Auto-detection: `llm fcio chat` renders markdown when stdout is a TTY,
skips it when piped. Override with `--markdown` / `--no-markdown`.

## File Ingestion Pipeline

`llm fcio ingest` turns files into embedding vectors:

1. **Discovery** — walks directories (or accepts explicit paths), applies
   `.gitignore` rules via `pathspec` plus a hardcoded exclude list
   (`venv/`, `node_modules/`, `__pycache__/`, etc.).
2. **Chunking** — splits files into line-based overlapping chunks. Chunk IDs
   encode file path and line range (`src/main.py:42-71`) so similarity search
   results point to specific locations.
3. **Embedding** — writes chunks into an `llm` collection via `sqlite-utils`
   with a Rich progress bar.

Defaults (30 lines, 5 overlap) target code files. Larger chunks suit prose.

## Model Resolution

The `chat` command supports fuzzy model resolution via `fzf`. When fzf is
installed, a substring triggers interactive selection. Without fzf, unambiguous
substrings still resolve. Ambiguous matches produce an error with the
candidates listed. The fzf subprocess gets a 10-second timeout to prevent
hanging.

## CLI Command Group

All commands live under `llm fcio`, registered as a `click` group. Each
command is a closure inside `register_commands`, capturing the CLI group
context. Commands that need API access retrieve the `Location` from the click
context (set by the `--location` option on the parent group).

## Quality Tooling

The project enforces code quality through tox environments, all runnable via `tox`:

| Env | Tool | Purpose |
|-----|------|---------|
| `lint` | ruff | Linting + format check |
| `type` | ty | Static type checking |
| `complexity` | complexipy | Cyclomatic complexity gate (CC ≤ 15) |
| `cov` | pytest | Tests with coverage report |

### Linting (ruff)

Ruff enforces a strict rule set: `E`, `F`, `I`, `N`, `W`, `UP`, `B`, `C4`, `SIM`, `TCH`,
`BLE`, `B904`, `ANN`, `RUF012`, `RET504`, `FURB110`, `PLW2901`. Target: Python 3.14,
line length 100. `BLE` and `B904` prevent bare exception hiding and missing error
chaining. `ANN` requires type annotations on all function signatures.

Run individually: `ruff check .` (lint), `ruff format --check .` (format).

### Type Coverage

All functions have full return type and parameter annotations. `llm` framework types
(`Prompt`, `Response`, `Conversation`) are imported from the `llm` package. Click
callbacks use `click.Context`, `click.Group`, etc.

### Complexity Gate

Every function must have cyclomatic complexity ≤ 15, enforced by `complexipy` in the
`tox` complexity environment. When a function exceeds the threshold, extract helper
functions following the pattern: private `_snake_case`, single responsibility, full
type annotations.

## Testing

Tests live in `tests/` with pytest. The `conftest.py` registers two markers:

- `live` — real API calls, requires `--run-live` flag
- `e2e` — end-to-end tests against real HTTP servers

Live tests are skipped by default to avoid API dependency in CI. Run the full suite
with `pytest` or via `tox -e cov`.

### Test Structure

| File | Scope |
|------|-------|
| `test_helpers.py` | Pure functions (`_chunk_lines`, `_discover_files`, `_build_chat_body`) |
| `test_streaming_renderer.py` | `_StreamingRenderer` block detection and rendering |
| `test_cli.py` | CLI commands with mocked HTTP |
| `test_integration.py` | Integration tests with mocked API |
| `test_properties.py` | Property-based tests via Hypothesis |
| `test_e2e_*.py` | End-to-end against real/dummy servers |
| `test_live.py` | Live API calls (`@pytest.mark.live`) |

Coverage target: reported via `--cov-report=term-missing`. Uncovered lines show in the
output.
