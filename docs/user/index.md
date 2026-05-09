# User Guide

:::{warning}
This plugin has **no automated test coverage**. Commands may behave differently than documented. If something doesn't work as expected, run `llm rzob health` to check connectivity, and consult the [CLI Reference](cli-reference.md) for exact command syntax.
:::

llm-fcio is a plugin for the [llm](https://llm.datasette.io/) CLI that connects to the FCIO RZOB API — an OpenAI-compatible endpoint on AMD hardware. It lets you chat with models, generate embeddings, and ingest files for semantic search.

## Getting Started

Prerequisites: [llm](https://llm.datasette.io/) must be installed.

1. Install the plugin: `llm install llm-fcio`
2. Set your API key: `llm keys set fcio-rzob` (or set the `FCIO_RZOB_API_KEY` environment variable)
3. Fetch available models: `llm rzob refresh`

## Common Tasks

### Ask a model a question

After `llm rzob refresh`, chat models work through the standard `llm` command:

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
llm rzob ingest my-project . --yes

# Search across the collection
llm similar my-project -c "how does error handling work"
```

See [Ingesting Files](cli-reference.md#llm-rzob-ingest) for chunking options, file discovery, and multi-format workflows.

### Check if the API is working

```bash
llm rzob health
```

### See available models

```bash
llm rzob models
llm rzob models --filter oss
```

## What This Plugin Does Not Do

- **Image or audio generation** — the API serves text chat completions and embeddings only
- **Function calling from the `llm` CLI** — the model options support it, but there is no CLI interface for tool definitions
- **Fine-tuning or training** — no training endpoints are exposed
- **Rate limit management** — the plugin sends requests directly; if you hit rate limits, you see the raw API error

```{toctree}
:hidden:

cli-reference
```
