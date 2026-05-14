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
llm fcio -l whq health            # health check for whq
```

## When not to use this

- You need image or audio generation — the API serves text and embeddings only.
- You need fine-tuning or training — no training endpoints are exposed.
- You need built-in rate limit handling — the plugin sends requests directly; rate limit errors surface as raw API errors.
- You need function calling from the CLI — the model options support it, but there is no CLI interface for tool definitions.

## Full documentation

→ [CLI Reference](docs/user/cli-reference.md) for all commands, flags, and options.
→ [User Guide](docs/user/index.md) for common tasks and workflows.

## Configuration

| Setting | Value |
|---|---|
| Default location | `rzob` |
| Model cache | `~/.llm/fcio_models_{location}.json` |
| Default chat model | `gpt-oss:20b` |
