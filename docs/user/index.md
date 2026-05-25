# User Guide

llm-fcio connects the [llm](https://llm.datasette.io/) CLI to the FCIO AI platform — an OpenAI-compatible endpoint on AMD hardware. Chat with models, generate embeddings, ingest files for semantic search.

## Getting Started

Prerequisites: [llm](https://llm.datasette.io/) must be installed.

1. Install the plugin: `llm install llm-fcio`
2. Set your API key: `llm keys set fcio-rzob` (or set `FCIO_RZOB_API_KEY`)
3. Fetch available models: `llm fcio refresh`

After step 3, all chat and embedding models work through the standard `llm` command.

## Common Tasks

### Ask a model a question

```bash
llm -m fcio-rzob/gpt-oss-20b "Explain transformers in three sentences"
```

### Have a conversation

```bash
llm chat -m fcio-rzob/gpt-oss-20b
```

### Set a system prompt

```bash
llm -m fcio-rzob/gpt-oss-120b -s "You are a helpful math tutor" "What is 2+2?"
```

### Adjust model behavior

```bash
llm -m fcio-rzob/gpt-oss-20b -o temperature 0.3 -o max_tokens 500 "Summarize this"
```

Options: `temperature` (0–2), `max_tokens`, `top_p` (0–1).

### Search your files with semantic search

```bash
# Ingest files into a collection
llm fcio ingest my-project . --yes

# Search across the collection
llm similar my-project -c "how does error handling work"
```

See [CLI Reference](cli-reference.md#llm-fcio-ingest) for chunking options and multi-format workflows.

### Analyze your code

```bash
# Code review of the current project
llm fcio analyze

# Project overview
llm fcio analyze overview
```

Zero-config: scans the current directory for code files and sends them to the model. Pass specific files or globs to narrow the scope.

See [CLI Reference](cli-reference.md#llm-fcio-analyze) for analysis types, templates, and options.

### See available models

```bash
llm fcio models
llm fcio models --filter oss
```

## What This Plugin Does Not Do

- **Image or audio generation** — text chat completions and embeddings only.
- **Function calling from the `llm` CLI** — the model options support it, but there is no CLI interface for tool definitions.
- **Fine-tuning or training** — no training endpoints are exposed.
- **Rate limit management** — requests go straight to the API; rate limit errors surface as raw API errors.

```{toctree}
:hidden:

cli-reference
python-api
```
