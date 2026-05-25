# llm-fcio

An [llm](https://llm.datasette.io/) CLI plugin for the [FCIO AI platform](https://ai.rzob.fcio.net/openai/v1) — OpenAI-compatible endpoints on AMD hardware, supporting multiple locations.

## Install

```bash
llm install llm-fcio
llm keys set fcio-rzob
```

Or set the `FCIO_RZOB_API_KEY` environment variable instead of `llm keys set`.

## Quick start

```bash
llm fcio refresh                  # fetch available models
llm -m fcio-rzob/gpt-oss-20b "Explain transformers in 3 sentences"
```

## Locations

| Location | API Base | Key name | Env variable |
|---|---|---|---|
| `rzob` | `https://ai.rzob.fcio.net/openai/v1` | `fcio-rzob` | `FCIO_RZOB_API_KEY` |
| `dev` | `https://ai.dev.fcio.net/openai/v1` | `fcio-dev` | `FCIO_DEV_API_KEY` |
| `whq` | `https://ai.whq.fcio.net/openai/v1` | `fcio-whq` | `FCIO_WHQ_API_KEY` |

Select a location with `-l`/`--location` (default: `rzob`). Model IDs include the location prefix: `fcio-rzob/...`, `fcio-dev/...`, `fcio-whq/...`.

```bash
llm fcio -l dev refresh           # refresh models for dev location
```

## Structured output

Models that support `response_format` (llama-server, vllm) accept JSON schema
for structured/typed responses via `llm prompt --schema`:

```bash
llm prompt --schema 'answer str' --no-stream -m fcio-dev/gpt-oss-20b "What is 2+2?"
# {"answer": "4"}
```

Check if a location's API accepts `response_format` with:

```bash
llm fcio capabilities     # look for "Schema output"
```

Per-model support depends on the inference backend — not all backends
implement `response_format`. Unsupported backends return an API error.

## When not to use this

- You need image or audio generation — the API serves text and embeddings only.
- You need fine-tuning or training — no training endpoints are exposed.
- You need built-in rate limit handling — the plugin sends requests directly; rate limit errors surface as raw API errors.
- You need function calling from the CLI — the model options support it, but there is no CLI interface for tool definitions.

## Python API

All plugin functions are directly importable — no Click context, no I/O side effects.

```python
import llm_fcio as fcio

models = fcio.refresh_models()
caps = fcio.get_capabilities()
count = fcio.ingest_files("mydocs", "./docs/", glob="*.md")
result = fcio.analyze_code("review", files=["src/main.py"])
```

→ [Python API Reference](docs/user/python-api.md) for all functions and parameters.

## Full documentation

→ [CLI Reference](docs/user/cli-reference.md) for all commands, flags, and options.
→ [User Guide](docs/user/index.md) for common tasks and workflows.

## Configuration

| Setting | Value |
|---|---|
| Default location | `rzob` |
| Model cache | `~/.llm/fcio_models_{location}.json` |
| Default chat model | `gpt-oss:20b` |
