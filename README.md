# llm-fcio

An [llm](https://llm.datasette.io/) CLI plugin for the [FCIO RZOB API](https://ai.rzob.fcio.net/openai/v1) — an OpenAI-compatible endpoint hosted on AMD hardware.

## Setup

```bash
# Install the plugin
llm install llm-fcio

# Set your API key
llm keys set fcio-rzob

# Fetch and cache available models
llm rzob refresh
```

Alternatively, set the `FCIO_RZOB_API_KEY` environment variable instead of using `llm keys set`.

## Chat Models

After running `llm rzob refresh`, chat models are available via the standard `llm` interface:

```bash
# One-shot prompt
llm -m fcio-rzob/gpt-oss-20b "Explain transformers in 3 sentences"

# With options
llm -m fcio-rzob/gpt-oss-20b -o temperature 0.3 -o max_tokens 500 "Summarize this"

# Interactive conversation
llm chat -m fcio-rzob/gpt-oss-20b

# System prompt
llm -m fcio-rzob/gpt-oss-120b -s "You are a helpful math tutor" "What is 2+2?"
```

### Available Chat Models

| Model ID | Alias | Description |
|---|---|---|
| `gpt-oss:20b` | `20b` | Default, fast |
| `gpt-oss:120b` | `120b` | Larger, more capable |
| `mistral-small3.2:latest` | `mistral` | Mistral Small 3.2 |

### Model Options

- `temperature` (float, 0–2) — sampling temperature
- `max_tokens` (int) — max tokens in response
- `top_p` (float, 0–1) — nucleus sampling

## Embedding Models

Embedding models integrate with `llm embed`, `llm embed-multi`, and `llm similar`:

```bash
# Single embedding
llm embed -m bge -c "Hello world"

# Batch embed files into a collection
llm embed-multi --files ./src "*.py" --collection my-code -m bge

# Semantic search
llm similar my-code -c "authentication logic"
```

### Available Embedding Models

| Model ID | Alias | Dimensions |
|---|---|---|
| `bge-m3:567m` | `bge` | 1024 |
| `Nomic-embed-text:v1.5` | `nomic` | — |
| `embeddinggemma:300m` | `gemma` | — |

### Ingesting Files with Chunking

`llm rzob ingest` splits files into line-based overlapping chunks, respects `.gitignore`, and embeds them into an llm collection:

```bash
# Ingest all markdown files in current directory (recursive)
llm rzob ingest my-project .

# Preview before embedding (default behavior)
llm rzob ingest my-project ./src/ --glob "*.py"

# Explicit files, custom chunk size
llm rzob ingest my-project README.md CONTRIBUTING.md --chunk-size 50

# Skip confirmation
llm rzob ingest my-project ./docs/ --yes

# Full options
llm rzob ingest my-project ./src/ --glob "*.py" -m bge --chunk-size 50 --overlap 10 --yes
```

Defaults: `--glob *.md`, `-m bge-m3-567m`, `--chunk-size 30`, `--overlap 5`. Files matching `.gitignore` patterns and common directories (`venv/`, `.venv/`, `node_modules/`, `__pycache__/`, `.git/`) are excluded automatically.

Chunk IDs encode file path and line range (e.g. `src/main.py:42-71`), enabling precise similarity results.

### Example: Ingesting a Python Project

```bash
# Create collection and ingest markdown docs
llm rzob ingest my-project . --glob "*.md" --yes

# Add Python source files to the same collection
llm rzob ingest my-project . --glob "*.py" --chunk-size 50 --overlap 10 --yes

# Search across both code and docs
llm similar my-project -c "how does error handling work"
```

Both invocations write into the same `my-project` collection. A single `llm similar` query then searches across code, tests, and documentation.

```bash
# Check what's in a collection
llm collections list

# Delete and rebuild
llm collections delete my-project
```

For simple one-file-one-embedding use cases, `llm embed-multi --files` still works (see above).

## CLI Commands

The plugin adds the `llm rzob` command group:

```bash
llm rzob refresh              # Fetch models from API, cache locally
llm rzob models               # List all models with type (chat/embed)
llm rzob models --filter oss  # Filter models by name
llm rzob models --json        # Raw JSON output

llm rzob chat "Hello"         # Quick chat (default: gpt-oss:20b)
llm rzob chat -m 120b "Hi"   # Use specific model
llm rzob chat -i              # Interactive chat mode
llm rzob chat -s "Be terse" "Explain DNS"

llm rzob embed bge "some text"            # Test embedding
llm rzob embed bge "text1" "text2" --json # Multiple texts, raw JSON

llm rzob ingest my-project .                         # Embed .md files (chunked, with preview)
llm rzob ingest my-project ./src/ --glob "*.py" --yes # Embed Python files, no preview

llm rzob health               # Check API connectivity and auth
llm rzob tokens gpt-oss:20b "count these tokens"
```

### Fuzzy Model Matching

Model names in `rzob chat` support fuzzy matching via [fzf](https://github.com/junegunn/fzf). Pass a substring and if there's a single match, it resolves automatically. Without fzf, substring matching still works for unambiguous cases.

## Configuration

| Setting | Value |
|---|---|
| API Base | `https://ai.rzob.fcio.net/openai/v1` |
| Key name | `fcio-rzob` (`llm keys set fcio-rzob`) |
| Env fallback | `FCIO_RZOB_API_KEY` |
| Model cache | `~/.llm/rzob_models.json` |
| Default chat model | `gpt-oss:20b` |
