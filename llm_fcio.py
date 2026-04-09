"""llm-rzob: Plugin für https://ai.rzob.fcio.net/openai/v1"""

import json
import subprocess
from pathlib import Path

import click
import httpx
from httpx_sse import connect_sse
import llm
import pathspec
from pydantic import Field
from collections.abc import Iterator
import sqlite_utils

API_BASE = "https://ai.rzob.fcio.net/openai/v1"
KEY_NAME = "fcio-rzob"


# ── API Utilities ──────────────────────────────────────


def get_api_key() -> str:
    """Holt den API-Key aus llm key store oder env"""
    key = llm.get_key("", KEY_NAME, "FCIO_RZOB_API_KEY")
    if not key:
        raise click.ClickException(
            f"API key not found. Set with: llm keys set {KEY_NAME}"
        )
    return key


def api_request(
    method: str,
    path: str,
    key: str,
    json_data: dict | None = None,
    params: dict | None = None,
) -> httpx.Response:
    """Generic API request helper mit Auth + Error-Handling"""
    url = f"{API_BASE}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.request(
            method,
            url,
            headers=headers,
            json=json_data,
            params=params,
        )
        if response.status_code >= 400:
            err = response.json()
            msg = err.get("detail", err.get("error", {}).get("message", str(err)))
            raise click.ClickException(f"{response.status_code}: {msg}")
        return response


# ── Model Cache ─────────────────────────────────────────


def _cache_path():
    return llm.user_dir() / "rzob_models.json"


def _load_models():
    p = _cache_path()
    if not p.exists():
        return []
    data = json.loads(p.read_text())
    # Migration: String-Liste → Dict-Liste
    if data and isinstance(data[0], str):
        data = [{"id": m, "safe_id": m} for m in data]
        p.write_text(json.dumps(data))
    return data


def _resolve_model(model_hint: str, key: str) -> str:
    """Resolve fuzzy model name to exact model ID via fzf."""
    resp = api_request("GET", "/models", key)
    all_ids = [m["id"] for m in resp.json().get("data", [])]

    if model_hint in all_ids:
        return model_hint

    try:
        result = subprocess.run(
            ["fzf", "-1", "--query", model_hint, "--no-mouse"],
            input="\n".join(all_ids),
            capture_output=True,
            text=True,
        )
        picked = result.stdout.strip()
        if picked:
            return picked
    except FileNotFoundError:
        # Fallback: substring match without fzf
        matches = [m for m in all_ids if model_hint.lower() in m.lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise click.ClickException(
                f"Ambiguous model '{model_hint}': {', '.join(matches)} "
                "(install fzf for interactive selection)"
            )

    raise click.ClickException(
        f"Unknown model '{model_hint}'. Available: {', '.join(all_ids)}"
    )


# ── Model Class ─────────────────────────────────────────


class RzobModel(llm.KeyModel):
    needs_key = KEY_NAME
    key_env_var = "FCIO_RZOB_API_KEY"
    can_stream = True

    class Options(llm.Options):
        temperature: float | None = Field(
            description="Sampling temperature (0-2)", ge=0.0, le=2.0, default=None
        )
        max_tokens: int | None = Field(
            description="Max tokens in response", ge=1, default=None
        )
        top_p: float | None = Field(
            description="Nucleus sampling parameter", ge=0.0, le=1.0, default=None
        )

    def __init__(self, model_id: str, api_id: str):
        self.model_id = model_id  # "fcio-rzob/gpt-oss-20b"
        self.api_id = api_id  # "gpt-oss-20b" für API-Call

    def __str__(self):
        return f"rzob: {self.api_id}"

    def execute(self, prompt, stream, response, conversation, key) -> Iterator[str]:
        messages = []
        if prompt.system:
            messages.append({"role": "system", "content": prompt.system})
        if conversation:
            for r in conversation.responses:
                if r.prompt.system:
                    messages.append({"role": "system", "content": r.prompt.system})
                if r.prompt.prompt:
                    messages.append({"role": "user", "content": r.prompt.prompt})
                messages.append({"role": "assistant", "content": r.text_or_raise()})
        if prompt.prompt:
            messages.append({"role": "user", "content": prompt.prompt})

        body = {"model": self.api_id, "messages": messages}
        if prompt.options.temperature is not None:
            body["temperature"] = prompt.options.temperature
        if prompt.options.max_tokens is not None:
            body["max_tokens"] = prompt.options.max_tokens
        if prompt.options.top_p is not None:
            body["top_p"] = prompt.options.top_p

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        if stream:
            body["stream"] = True
            with httpx.Client() as client:
                with connect_sse(
                    client,
                    "POST",
                    f"{API_BASE}/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=None,
                ) as es:
                    es.response.raise_for_status()
                    for sse in es.iter_sse():
                        if sse.data == "[DONE]":
                            continue
                        try:
                            event = json.loads(sse.data)
                            delta = event["choices"][0].get("delta", {})
                            if delta.get("content"):
                                yield delta["content"]
                        except (KeyError, json.JSONDecodeError):
                            continue
        else:
            with httpx.Client() as client:
                resp = client.post(
                    f"{API_BASE}/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=None,
                )
                resp.raise_for_status()
                data = resp.json()
                yield data["choices"][0]["message"]["content"]
                response.response_json = data


# ── Embedding Model ──────────────────────────────────────


class RzobEmbeddingModel(llm.EmbeddingModel):
    needs_key = KEY_NAME
    key_env_var = "FCIO_RZOB_API_KEY"
    batch_size = 100

    def __init__(self, model_id: str, api_id: str):
        self.model_id = model_id
        self.api_id = api_id

    def embed_batch(self, items):
        key = self.get_key()
        resp = httpx.post(
            f"{API_BASE}/embeddings",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={"model": self.api_id, "input": list(items)},
            timeout=30.0,
        )
        resp.raise_for_status()
        for entry in resp.json()["data"]:
            yield entry["embedding"]


# ── Model Registration ──────────────────────────────────


_SHORT_CHAT_ALIASES = {
    "gpt-oss:20b": "20b",
    "gpt-oss:120b": "120b",
    "mistral-small3.2:latest": "mistral",
}

_SHORT_EMBED_ALIASES = {
    "bge-m3:567m": "bge",
    "Nomic-embed-text:v1.5": "nomic",
    "embeddinggemma:300m": "gemma",
}


@llm.hookimpl
def register_models(register):
    for m in _load_models():
        mid = m["id"]
        safe = m.get("safe_id", mid.replace(":", "-"))
        short = _SHORT_CHAT_ALIASES.get(mid)
        aliases = [safe] + ([short] if short else [])
        register(
            RzobModel(f"fcio-rzob/{safe}", mid),
            aliases=aliases,
        )


@llm.hookimpl
def register_embedding_models(register):
    embed_keywords = ("embed", "bge", "gemma")
    for m in _load_models():
        mid = m["id"]
        if not any(k in mid.lower() for k in embed_keywords):
            continue
        safe = m.get("safe_id", mid.replace(":", "-"))
        short = _SHORT_EMBED_ALIASES.get(mid)
        aliases = [safe] + ([short] if short else [])
        register(
            RzobEmbeddingModel(f"fcio-rzob/{safe}", mid),
            aliases=aliases,
        )


# ── Ingest Helpers ──────────────────────────────────────


_HARD_EXCLUDES = [
    "venv/",
    ".venv/",
    "node_modules/",
    "__pycache__/",
    ".git/",
    "*.egg-info/",
]


def _discover_files(
    paths: tuple[Path, ...],
    glob_pattern: str,
) -> list[Path]:
    """Discover files from paths, applying gitignore + hard-exclude filtering."""
    all_files: list[Path] = []
    for p in paths:
        p = p.resolve()
        if p.is_file():
            all_files.append(p)
        elif p.is_dir():
            gitignore_path = p / ".gitignore"
            spec_lines: list[str] = []
            if gitignore_path.exists():
                spec_lines = gitignore_path.read_text().splitlines()
            spec_lines.extend(_HARD_EXCLUDES)
            spec = pathspec.PathSpec.from_lines("gitwildmatch", spec_lines)

            for candidate in sorted(p.rglob(glob_pattern)):
                rel = candidate.relative_to(p)
                if not spec.match_file(str(rel)):
                    all_files.append(candidate)
        else:
            raise click.ClickException(f"Path not found: {p}")
    return all_files


def _chunk_lines(
    text: str,
    filepath: str,
    chunk_size: int,
    overlap: int,
) -> list[tuple[str, str]]:
    """Split text into line-based overlapping chunks.

    Returns list of (chunk_id, chunk_text) tuples.
    Chunk ID format: 'filepath:start-end' (1-based lines).
    """
    lines = text.splitlines()
    if not lines:
        return []
    chunks: list[tuple[str, str]] = []
    start = 0
    while start < len(lines):
        end = min(start + chunk_size, len(lines))
        chunk_text = "\n".join(lines[start:end])
        chunk_id = f"{filepath}:{start + 1}-{end}"
        chunks.append((chunk_id, chunk_text))
        if end >= len(lines):
            break
        start += chunk_size - overlap
    return chunks


# ── CLI Commands ────────────────────────────────────────


@llm.hookimpl
def register_commands(cli):

    @cli.group()
    def rzob():
        "Commands for the llm-rzob plugin"

    # ── refresh ────────────────────────────────────────

    @rzob.command()
    def refresh():
        """Fetch available models from API and cache locally"""
        key = get_api_key()
        resp = api_request("GET", "/models", key)
        raw = resp.json()
        data = raw.get("data", raw if isinstance(raw, list) else [])
        models = []
        for m in data:
            mid = m["id"] if isinstance(m, dict) else str(m)
            models.append(
                {
                    "id": mid,
                    "safe_id": mid.replace(":", "-").replace(".", "_"),
                }
            )
        _cache_path().write_text(json.dumps(models, indent=2))
        click.echo(f"Cached {len(models)} models", err=True)

    # ── models ─────────────────────────────────────────

    @rzob.command("models")
    @click.option("--json", "as_json", is_flag=True, help="Output as raw JSON")
    @click.option("--filter", "filt", help="Filter models by name substring")
    def cmd_models(as_json: bool, filt: str | None):
        """List available models from the API"""
        key = get_api_key()
        resp = api_request("GET", "/models", key)
        models = resp.json().get("data", [])

        if filt:
            models = [m for m in models if filt.lower() in m.get("id", "").lower()]

        if as_json:
            click.echo(json.dumps(models, indent=2))
        else:
            click.echo(f"{'Type':>10}  {'ID'}")
            click.echo("-" * 55)
            for m in models:
                mid = m.get("id", "unknown")
                mtype = (
                    "embed"
                    if any(k in mid.lower() for k in ("embed", "bge", "gemma"))
                    else "chat"
                )
                click.echo(f"{mtype:>10}  {mid}")

    # ── chat ───────────────────────────────────────────

    @rzob.command("chat")
    @click.argument("prompt", nargs=-1, required=False)
    @click.option("-m", "--model", "model_id", default="gpt-oss:20b", help="Model ID")
    @click.option("-s", "--system", help="System prompt")
    @click.option("-t", "--temperature", type=float, default=0.7, help="Temperature")
    @click.option("--max-tokens", type=int, help="Max tokens")
    @click.option("--stream/--no-stream", default=True, help="Stream response")
    @click.option("--json", "as_json", is_flag=True, help="Output full JSON response")
    @click.option("-i", "--interactive", is_flag=True, help="Interactive chat mode")
    def cmd_chat(
        prompt: tuple[str],
        model_id: str,
        system: str | None,
        temperature: float,
        max_tokens: int | None,
        stream: bool,
        as_json: bool,
        interactive: bool,
    ):
        """Test chat completions with a model"""
        key = get_api_key()
        model_id = _resolve_model(model_id, key)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})

        prompt_text = " ".join(prompt) if prompt else None

        if interactive:
            click.echo(f"Interactive chat with {model_id} (Ctrl+D to exit)")
            click.echo("-" * 50)
            while True:
                try:
                    user_input = click.prompt("You", prompt_suffix="> ", default="")
                    if not user_input.strip():
                        continue
                    messages.append({"role": "user", "content": user_input})
                except EOFError:
                    click.echo("\nGoodbye!")
                    break

                body = _build_chat_body(model_id, messages, temperature, max_tokens)
                _send_chat_request(key, body, stream, as_json)
                messages.append({"role": "assistant", "content": "[...]"})
        else:
            if not prompt_text:
                raise click.ClickException("Prompt required (or use --interactive)")
            messages.append({"role": "user", "content": prompt_text})
            body = _build_chat_body(model_id, messages, temperature, max_tokens)
            _send_chat_request(key, body, stream, as_json)

    # ── embed ──────────────────────────────────────────

    @rzob.command("embed")
    @click.argument("model_id")
    @click.argument("text", nargs=-1, required=True)
    @click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
    @click.option("-d", "--dimensions", type=int, help="Output dimension")
    def cmd_embed(
        model_id: str, text: tuple[str], as_json: bool, dimensions: int | None
    ):
        """Test embedding generation"""
        key = get_api_key()

        body = {
            "model": model_id,
            "input": list(text) if len(text) > 1 else text[0],
        }
        if dimensions:
            body["dimensions"] = dimensions

        resp = api_request("POST", "/embeddings", key, json_data=body)
        data = resp.json()

        if as_json:
            click.echo(json.dumps(data, indent=2))
        else:
            embeddings = data.get("data", [])
            for i, emb in enumerate(embeddings):
                vec = emb.get("embedding", [])
                click.echo(f"Text {i + 1}: [{len(vec)} dims] {vec[:5]}... (truncated)")
                click.echo(f"  Usage: {emb.get('usage', {})}")

    # ── health ─────────────────────────────────────────

    @rzob.command("health")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON")
    def cmd_health(as_json: bool):
        """Check API health and auth"""
        key = get_api_key()

        results = {}

        # Auth test via /models
        try:
            resp = api_request("GET", "/models", key)
            results["auth"] = "✅ valid"
            results["models_count"] = len(resp.json().get("data", []))
        except click.ClickException as e:
            results["auth"] = f"❌ {e}"
            results["models_count"] = None

        # Base URL reachable
        try:
            r = httpx.get(API_BASE.replace("/v1", ""), timeout=5)
            results["base_url"] = f"✅ {r.status_code}"
        except Exception as e:
            results["base_url"] = f"❌ {e}"

        # Chat endpoint smoke test
        try:
            body = {"model": "test", "messages": [{"role": "user", "content": "."}]}
            resp = api_request("POST", "/chat/completions", key, json_data=body)
            if resp.status_code == 401:
                results["chat_endpoint"] = "❌ auth failed"
            elif resp.status_code == 404:
                results["chat_endpoint"] = "⚠️  endpoint not found"
            else:
                results["chat_endpoint"] = f"✅ {resp.status_code}"
        except click.ClickException as e:
            if "400" in str(e) and "model" in str(e).lower():
                results["chat_endpoint"] = "✅ reachable (model error expected)"
            else:
                results["chat_endpoint"] = f"❌ {e}"

        if as_json:
            click.echo(json.dumps(results, indent=2))
        else:
            click.echo("🔍 FCIO RZOB API Health")
            click.echo("-" * 40)
            for k, v in results.items():
                click.echo(f"{k:<20} {v}")

    # ── tokens ─────────────────────────────────────────

    @rzob.command("tokens")
    @click.argument("model_id")
    @click.argument("text", nargs=-1, required=True)
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON")
    def cmd_tokens(model_id: str, text: tuple[str], as_json: bool):
        """Estimate token count for text (if endpoint supports it)"""
        key = get_api_key()

        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": " ".join(text)}],
            "max_tokens": 1,
        }

        try:
            resp = api_request("POST", "/chat/completions", key, json_data=body)
            data = resp.json()
            usage = data.get("usage", {})

            if as_json:
                click.echo(json.dumps(usage, indent=2))
            else:
                click.echo(f"Model: {model_id}")
                click.echo(f"Prompt tokens:     {usage.get('prompt_tokens', '?')}")
                click.echo(f"Completion tokens: {usage.get('completion_tokens', '?')}")
                click.echo(f"Total:             {usage.get('total_tokens', '?')}")
        except click.ClickException as e:
            click.echo(f"⚠️  Token endpoint not supported: {e}", err=True)
            total_chars = sum(len(t) for t in text)
            click.echo(f"Rough estimate: ~{total_chars // 4} tokens (heuristic)")

    # ── ingest ─────────────────────────────────────────

    @rzob.command("ingest")
    @click.argument("collection")
    @click.argument("paths", nargs=-1, required=True)
    @click.option(
        "--glob",
        "glob_pattern",
        default="*.md",
        help="File glob for directory discovery (default: *.md)",
    )
    @click.option(
        "-m",
        "--model",
        "model_id",
        default="bge-m3-567m",
        help="Embedding model alias (default: bge-m3-567m)",
    )
    @click.option(
        "--chunk-size", type=int, default=30, help="Lines per chunk (default: 30)"
    )
    @click.option(
        "--overlap",
        type=int,
        default=5,
        help="Overlap lines between chunks (default: 5)",
    )
    @click.option(
        "--yes", "skip_confirm", is_flag=True, help="Skip confirmation preview"
    )
    def cmd_ingest(
        collection: str,
        paths: tuple[str, ...],
        glob_pattern: str,
        model_id: str,
        chunk_size: int,
        overlap: int,
        skip_confirm: bool,
    ):
        """Ingest files into an llm embedding collection.

        COLLECTION is the collection name. PATHS are directories (recursive
        discovery) or explicit files.

        \b
        Examples:
          llm rzob ingest mydocs ./docs/
          llm rzob ingest mydocs ./docs/ --glob '*.py'
          llm rzob ingest mydocs file1.md file2.md
          llm rzob ingest mydocs ./src/ -m bge --chunk-size 50 --overlap 10
        """
        resolved_paths = tuple(Path(p) for p in paths)
        files = _discover_files(resolved_paths, glob_pattern)

        if not files:
            raise click.ClickException("No files found matching criteria")

        # Build chunk map: {filepath: [(chunk_id, chunk_text), ...]}
        file_chunks: dict[str, list[tuple[str, str]]] = {}
        for f in files:
            text = f.read_text(errors="replace")
            display_path = str(f)
            chunks = _chunk_lines(text, display_path, chunk_size, overlap)
            if chunks:
                file_chunks[display_path] = chunks

        total_chunks = sum(len(cs) for cs in file_chunks.values())

        if not skip_confirm:
            click.echo("Files to ingest:")
            max_name_len = max(len(n) for n in file_chunks)
            for name, chunks in file_chunks.items():
                padded = name.ljust(max_name_len)
                click.echo(f"  {padded}  {len(chunks)} chunks")
            click.echo(f"Total: {len(file_chunks)} files, {total_chunks} chunks")
            click.echo()
            if not click.confirm("Continue", default=False):
                raise click.ClickException("Aborted")

        # Create collection and embed
        db = sqlite_utils.Database(llm.user_dir() / "embeddings.db")
        if llm.Collection.exists(db, collection):
            col = llm.Collection(collection, db)
        else:
            col = llm.Collection(collection, db=db, model_id=model_id)
        click.echo(f"Using model: {col.model().model_id}", err=True)

        for name, chunks in file_chunks.items():
            click.echo(f"  {name} ({len(chunks)} chunks)...", err=True)
            col.embed_multi(
                ((cid, text) for cid, text in chunks),
                store=True,
            )

        click.echo(f"Ingested {total_chunks} chunks into '{collection}'", err=True)


# ── Chat Helpers ────────────────────────────────────────


def _build_chat_body(
    model_id: str,
    messages: list,
    temperature: float,
    max_tokens: int | None,
) -> dict:
    body = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    return body


def _send_chat_request(key: str, body: dict, stream: bool, as_json: bool):
    if stream:
        body["stream"] = True
        try:
            with connect_sse(
                httpx.Client(),
                "POST",
                f"{API_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=None,
            ) as event_source:
                event_source.response.raise_for_status()
                for sse in event_source.iter_sse():
                    if sse.data == "[DONE]":
                        continue
                    try:
                        event = json.loads(sse.data)
                        delta = event["choices"][0].get("delta", {})
                        if delta.get("content"):
                            click.echo(delta["content"], nl=False)
                    except (KeyError, json.JSONDecodeError):
                        continue
                click.echo()
        except click.ClickException:
            raise
        except Exception as e:
            raise click.ClickException(f"Streaming error: {e}")
    else:
        resp = api_request("POST", "/chat/completions", key, json_data=body)
        data = resp.json()
        if as_json:
            click.echo(json.dumps(data, indent=2))
        else:
            content = data["choices"][0]["message"]["content"]
            click.echo(content)
            if "usage" in data:
                u = data["usage"]
                click.echo(
                    f"\n⚡ {u.get('prompt_tokens', '?')}→{u.get('completion_tokens', '?')} tokens",
                    err=True,
                )
