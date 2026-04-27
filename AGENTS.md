# AGENTS.md

**Generated:** 2026-04-27

Full docs: [README.md](./README.md)

## Project
- `llm-fcio`: `llm` CLI plugin, FCIO RZOB API (OpenAI-compatible)
- Entry: `llm_fcio.py` (plugin, models, CLI commands)
- Python 3.14, managed via `uv`
- Env var: `FCIO_RZOB_API_KEY` (required)

## Commands
- Install/dev: `uv pip install -e .`
- Refresh models: `llm rzob refresh` (caches to `~/.llm/rzob_models.json`)
- All CLI commands: see [README.md](./README.md)

## Top-Level Structure
- `llm_fcio.py`: Single code file (plugin implementation)
- `pyproject.toml`: Project config (uv, dependencies)
- `uv.lock`: Dependency lockfile
- Runtime: `~/.llm/rzob_models.json` (model cache, auto-created)

## Conventions
- Single-file plugin (no `src/`, tests, or extra modules)
- `llm` key name: `fcio-rzob` (used with `llm keys set`)
- Cache location: `~/.llm/rzob_models.json` (not versioned)
