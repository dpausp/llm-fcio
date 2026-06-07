# AGENTS.md

`llm` CLI plugin for FCIO RZOB API (OpenAI-compatible).

Full docs: [README.md](./README.md)

## Setup
- Python, managed via `uv`
- Env var: `FCIO_RZOB_API_KEY` (required)
- `llm` key name: `fcio-rzob`

## Architecture
- Entry point: `llm_fcio.py`

## Workflow
- Tests before code: no new code without a clear test situation first. Update or write tests, then implement.
