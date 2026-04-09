# AGENTS.md

- **Project**: `llm-fcio` — `llm` CLI plugin for FCIO RZOB API (OpenAI-compatible)
- **Single file**: `llm_fcio.py` — plugin entrypoint, model class, CLI commands
- **Python 3.14**, managed via `uv`
- **Install/dev**: `uv pip install -e .`
- **Key env var**: `FCIO_RZOB_API_KEY`
- **Refresh models**: `llm rzob refresh` (fetches from API, caches to `~/.llm/rzob_models.json`)
- **Dependencies**: `llm`, `httpx`, `httpx-sse`, `click`, `pydantic`
