# Architecture

llm-fcio is a single-file `llm` CLI plugin (`llm_fcio.py`) that connects to an
OpenAI-compatible API endpoint. It registers chat models, embedding models, and
CLI commands through the `llm` plugin hook system.

## Plugin Hook System

The `llm` framework discovers plugins via the
`[project.entry-points.llm]` entry point. Each decorated function registers a
capability:

- `register_models` — chat models available through `llm -m fcio-rzob/...`
- `register_embedding_models` — embedding models for `llm embed`
- `register_commands` — the `llm rzob` command group

All three hooks read from the same model cache. If the cache is empty (no
`refresh` run yet), nothing registers and the plugin is inert.

## Error Handling

Programmatic errors use two custom exception classes:

- `ModelError(Exception)` — ambiguous or unknown model resolution failures
- `ApiError(Exception)` — API communication failures (empty response, streaming errors, HTTP status errors)

CLI-user-facing validation errors (missing prompt, path not found, no files, aborted) remain `click.ClickException` since they serve CLI UX, not programmatic error handling.

## Model Registration

Models are not hardcoded. The plugin fetches available models from the API at
runtime and caches them as JSON. Registration then iterates the cache:

1. **Chat models**: every cached model ID gets a `RzobModel` instance.
   Models with known aliases (defined in `_SHORT_CHAT_ALIASES`) gain short
   names for convenience.
2. **Embedding models**: models whose ID contains `embed`, `bge`, or `gemma`
   are registered as `RzobEmbeddingModel` instances instead.

The dual registration means the same model cache drives both `llm -m` and
`llm embed -m`. The embedding keyword filter determines which models go where.

### Safe ID Mapping

API model IDs contain characters illegal in `llm` model names (colons, dots).
The plugin generates a `safe_id` by replacing `:` → `-` and `.` → `_`. The
`llm` model ID is always `fcio-rzob/<safe_id>`, while the API call uses the
original ID.

### Model Options Forwarding

The `RzobModel.Options` class declares `tools` and `response_format` fields
via pydantic `Field()`. Both are forwarded in `execute()` to the API request body
when set — tool calling and JSON mode are functional, not silently ignored.

## API Communication

All API calls go through `httpx` with Bearer token auth. The key resolution
chain: `llm` key store → `FCIO_RZOB_API_KEY` env var → error.

Chat completions support SSE streaming via `httpx-sse`. The SSE parsing logic
is encapsulated in `_iter_sse_content(client, url, headers, body) -> Iterator[str]`,
a shared generator that handles the connection, iteration, JSON parsing, and
delta extraction. Both `execute()` and `_send_chat_request()` delegate to this
generator, eliminating duplicated SSE loops. The generator parses `data: [DONE]`
termination and yields content deltas incrementally. Non-streaming calls use the
same endpoint without the `stream` parameter.

HTTP status codes use `httpx.codes` named constants (`BAD_REQUEST`, `UNAUTHORIZED`,
`NOT_FOUND`) instead of raw integers. Status threshold checks use
`>= httpx.codes.BAD_REQUEST`.

Embedding calls are batched (`batch_size = 100`). Each batch posts to
`/embeddings` and yields vectors from the response.

## Model Cache

The cache file (`~/.llm/rzob_models.json`) bridges API discovery and plugin
registration. It is populated by `llm rzob refresh` and read by every other
operation that needs model metadata.

The cache includes a migration path: older caches stored plain string lists,
current caches store dicts with `id` and `safe_id` keys. The loader handles
both formats transparently.

## File Ingestion Pipeline

`llm rzob ingest` implements a file-to-embedding pipeline:

1. **Discovery**: walks directories (or accepts explicit file paths), applies
   `.gitignore` rules via `pathspec` plus a hardcoded exclude list (venv,
   node_modules, etc.).
2. **Chunking**: splits files into line-based overlapping chunks. Chunk IDs
   encode the file path and line range (e.g. `src/main.py:42-71`), which makes
   similarity search results point to specific file locations.
3. **Embedding**: writes chunks into an `llm` collection via `sqlite-utils`.
   A Rich progress bar tracks per-chunk progress.

The chunk size and overlap are configurable. The default (30 lines / 5 overlap)
targets code files; larger chunks suit prose documents.

## Testing

Tests live in `tests/` using pytest. The initial test suite covers three pure
functions (`_chunk_lines`, `_discover_files`, `_build_chat_body`) without mocking.
Test configuration is in `[tool.pytest.ini_options]` in `pyproject.toml` with
coverage reporting via `pytest-cov`.

## CLI Command Group

All plugin CLI commands live under `llm rzob`, registered as a `click` group.
The pattern is: each command is a closure inside `register_commands`, capturing
the `cli` group context. Commands that need the API key call `get_api_key()`
directly.

The `chat` command supports fuzzy model resolution via `fzf`. The `fzf` binary is
resolved via `shutil.which("fzf")` before the subprocess call, with a 10-second
timeout to prevent hanging. When fzf is installed, a substring triggers interactive
selection. Without fzf, unambiguous substrings still resolve. Ambiguous matches
produce an error.

## Configuration Points

| What | Where | Purpose |
|------|-------|---------|
| API key | `llm keys set fcio-rzob` or `FCIO_RZOB_API_KEY` | Authentication |
| Model cache | `~/.llm/rzob_models.json` | API model discovery bridge |
| Embedding DB | `~/.llm/embeddings.db` | `llm` managed, used by ingest |
| Hard excludes | `_HARD_EXCLUDES` in source | Directories never ingested |
| Model aliases | `_SHORT_CHAT_ALIASES`, `_SHORT_EMBED_ALIASES` | Short names for known models |
| Ruff config | `[tool.ruff]` in `pyproject.toml` | Lint rules, target Python 3.14 |
| Test runner | `tests/`, pytest config in `pyproject.toml` | Pure function coverage |

The API base URL and key name are constants (`API_BASE`, `KEY_NAME`).
No plugin-level config file exists; all configuration flows through `llm`'s
own mechanisms.
