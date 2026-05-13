"""llm-fcio: Plugin für FCIO AI platform (multi-location)"""

import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import click
import httpx
import llm
import pathspec
import sqlite_utils
from httpx_sse import connect_sse
from pydantic import Field
from rich.console import Console
from rich.markdown import Markdown
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.syntax import Syntax
from rich.text import Text

# ── Location Configuration ──────────────────────────────


@dataclass(slots=True, frozen=True)
class Location:
    name: str
    api_base: str
    key_name: str
    env_var: str


LOCATIONS: dict[str, Location] = {
    "rzob": Location(
        "rzob", "https://ai.rzob.fcio.net/openai/v1", "fcio-rzob", "FCIO_RZOB_API_KEY"
    ),
    "dev": Location("dev", "https://ai.dev.fcio.net/openai/v1", "fcio-dev", "FCIO_DEV_API_KEY"),
    "whq": Location("whq", "https://ai.whq.fcio.net/openai/v1", "fcio-whq", "FCIO_WHQ_API_KEY"),
}
DEFAULT_LOCATION = "rzob"


class ModelError(Exception):
    """Model resolution errors (ambiguous/unknown model)."""


class ApiError(Exception):
    """API communication errors (empty response, streaming, status)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── API Utilities ──────────────────────────────────────


def get_api_key(loc: Location) -> str:
    """Holt den API-Key aus llm key store oder env"""
    key = llm.get_key("", loc.key_name, loc.env_var)
    if not key:
        raise click.ClickException(
            f"API key not found. Set with: llm keys set {loc.key_name}",
        )
    return key


def api_request(
    method: str,
    path: str,
    key: str,
    api_base: str,
    json_data: dict | None = None,
    params: dict | None = None,
) -> httpx.Response:
    """Generic API request helper mit Auth + Error-Handling"""
    url = f"{api_base}/{path.lstrip('/')}"
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
        if response.status_code >= httpx.codes.BAD_REQUEST:
            err = response.json()
            msg = err.get("detail", err.get("error", {}).get("message", str(err)))
            raise ApiError(f"{response.status_code}: {msg}", status_code=response.status_code)
        return response


def _iter_sse_content(
    client: httpx.Client,
    url: str,
    headers: dict,
    body: dict,
) -> Iterator[str]:
    """Yield content deltas from an SSE streaming response."""
    with connect_sse(
        client,
        "POST",
        url,
        headers=headers,
        json=body,
        timeout=None,
    ) as event_source:
        event_source.response.raise_for_status()
        for sse in event_source.iter_sse():
            if sse.data == "[DONE]":
                continue
            try:
                event = json.loads(sse.data)
                choices = event.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                if delta.get("content"):
                    yield delta["content"]
            except KeyError, json.JSONDecodeError, IndexError:
                continue


# ── Model Cache ─────────────────────────────────────────


def _cache_path(loc_name: str = DEFAULT_LOCATION) -> Path:
    new_path = llm.user_dir() / f"fcio_models_{loc_name}.json"
    if not new_path.exists() and loc_name == DEFAULT_LOCATION:
        old_path = llm.user_dir() / "rzob_models.json"
        if old_path.exists():
            old_path.rename(new_path)
    return new_path


def _load_models(loc_name: str = DEFAULT_LOCATION) -> list[dict]:
    p = _cache_path(loc_name)
    if not p.exists():
        return []
    data = json.loads(p.read_text())
    # Migration: String-Liste → Dict-Liste
    if data and isinstance(data[0], str):
        data = [{"id": m, "safe_id": m} for m in data]
        p.write_text(json.dumps(data))
    return data


def _resolve_model(model_hint: str, key: str, api_base: str) -> str:
    """Resolve fuzzy model name to exact model ID via fzf."""
    resp = api_request("GET", "/models", key, api_base)
    all_ids = [m["id"] for m in resp.json().get("data", [])]

    if model_hint in all_ids:
        return model_hint

    fzf_path = shutil.which("fzf")
    if fzf_path:
        try:
            result = subprocess.run(
                [fzf_path, "-1", "--query", model_hint, "--no-mouse"],
                input="\n".join(all_ids),
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            picked = result.stdout.strip()
            if picked:
                return picked
        except FileNotFoundError:
            pass

    # Fallback: substring match without fzf
    matches = [m for m in all_ids if model_hint.lower() in m.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ModelError(
            f"Ambiguous model '{model_hint}': {', '.join(matches)} "
            "(install fzf for interactive selection)",
        ) from None

    raise ModelError(
        f"Unknown model '{model_hint}'. Available: {', '.join(all_ids)}",
    )


# ── Model Class ─────────────────────────────────────────


class RzobModel(llm.KeyModel):
    can_stream = True
    attachment_types = {"text/plain"}

    class Options(llm.Options):
        temperature: float | None = Field(
            description="Sampling temperature (0-2)",
            ge=0.0,
            le=2.0,
            default=None,
        )
        max_tokens: int | None = Field(
            description="Max tokens in response",
            ge=1,
            default=None,
        )
        top_p: float | None = Field(
            description="Nucleus sampling parameter",
            ge=0.0,
            le=1.0,
            default=None,
        )
        # Tools for function calling (gpt-oss native)
        tools: list[dict] | None = Field(
            description="List of function definitions for tool calling",
            default=None,
        )
        # Force JSON/structured output
        response_format: dict | None = Field(
            description='Response format (e.g. {"type": "json_object"})',
            default=None,
        )

    def __init__(self, model_id: str, api_id: str, location: Location) -> None:
        self.model_id = model_id
        self.api_id = api_id
        self.needs_key = location.key_name
        self.key_env_var = location.env_var
        self._location = location

    def __str__(self) -> str:
        return f"{self._location.name}: {self.api_id}"

    def execute(
        self,
        prompt: llm.Prompt,
        stream: bool,
        response: llm.Response,
        conversation: llm.Conversation | None,
        key: str,
    ) -> Iterator[str]:
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
        # Build user message with attachments
        user_content_parts = []
        for att in prompt.attachments or []:
            att_type = att.resolve_type()
            if att_type == "text/plain":
                text = att.content_bytes().decode("utf-8")
                user_content_parts.append({"type": "text", "text": text})
        if prompt.prompt:
            user_content_parts.append({"type": "text", "text": prompt.prompt})
        if user_content_parts:
            content = (
                user_content_parts[0]["text"]
                if len(user_content_parts) == 1
                else user_content_parts
            )
            messages.append({"role": "user", "content": content})

        body = {"model": self.api_id, "messages": messages}
        if prompt.options.temperature is not None:
            body["temperature"] = prompt.options.temperature
        if prompt.options.max_tokens is not None:
            body["max_tokens"] = prompt.options.max_tokens
        if prompt.options.top_p is not None:
            body["top_p"] = prompt.options.top_p
        if prompt.options.tools is not None:
            body["tools"] = prompt.options.tools
        if prompt.options.response_format is not None:
            body["response_format"] = prompt.options.response_format

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        api_base = self._location.api_base
        if stream:
            body["stream"] = True
            with httpx.Client() as client:
                url = f"{api_base}/chat/completions"
                for content in _iter_sse_content(client, url, headers, body):
                    yield content
        else:
            with httpx.Client() as client:
                resp = client.post(
                    f"{api_base}/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=None,
                )
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices") or []
                if not choices:
                    raise ApiError(
                        "Empty response from API - no choices returned",
                    )
                msg = choices[0].get("message") or {}
                content = msg.get("content") or ""
                yield content
                response.response_json = data


# ── Embedding Model ──────────────────────────────────────


class RzobEmbeddingModel(llm.EmbeddingModel):
    batch_size = 100

    def __init__(self, model_id: str, api_id: str, location: Location) -> None:
        self.model_id = model_id
        self.api_id = api_id
        self.needs_key = location.key_name
        self.key_env_var = location.env_var
        self._location = location

    def embed_batch(self, items: Iterator[str | bytes]) -> Iterator[list[float]]:
        key = self.get_key()
        api_base = self._location.api_base
        resp = httpx.post(
            f"{api_base}/embeddings",
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


# ── Streaming Markdown Renderer ─────────────────────────


@dataclass(slots=True)
class _Block:
    kind: str  # "text" | "code"
    content: list[str] = field(default_factory=list)
    language: str | None = None


class _StreamingRenderer:
    """Accumulate streaming markdown, render finalized blocks with Rich.

    Feeds chunks (arbitrary byte boundaries) into a line buffer,
    detects block boundaries (blank lines, code fences), and renders
    each finalized block immediately via Rich Console — no pipe, no buffering.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._active = _Block(kind="text")
        self._in_code_fence = False
        self._console = Console(force_terminal=True)

    def feed(self, chunk: str) -> None:
        """Feed a chunk of markdown. Renders any finalized blocks."""
        self._buf += chunk
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            for block in self._detect(line):
                self._render(block)

    def flush(self) -> None:
        """Flush remaining buffer + active block."""
        if self._buf.strip():
            for block in self._detect(self._buf):
                self._render(block)
            self._buf = ""
        for block in self._finalize_active():
            self._render(block)

    # -- block detection --

    def _detect(self, line: str) -> list[_Block]:
        stripped = line.rstrip("\r")
        ready: list[_Block] = []

        if self._in_code_fence:
            if stripped.strip() == "```":
                ready.append(self._active)
                self._active = _Block(kind="text")
                self._in_code_fence = False
            else:
                self._active.content.append(stripped)
            return ready

        if stripped.startswith("```"):
            if self._active.content and any(line.strip() for line in self._active.content):
                ready.append(self._active)
            lang = stripped[3:].strip() or None
            self._active = _Block(kind="code", language=lang)
            self._in_code_fence = True
            return ready

        if stripped == "" and self._active.content:
            if any(line.strip() for line in self._active.content):
                ready.append(self._active)
                self._active = _Block(kind="text")
            return ready

        self._active.content.append(stripped)
        return ready

    def _finalize_active(self) -> list[_Block]:
        if self._active.content and any(line.strip() for line in self._active.content):
            block = self._active
            self._active = _Block(kind="text")
            return [block]
        return []

    # -- rendering --

    def _render(self, block: _Block) -> None:
        if block.kind == "code":
            code = "\n".join(block.content)
            if not code.strip():
                return
            lang = block.language
            if lang and lang.lower() not in ("", "text", "plain"):
                try:
                    self._console.print(
                        Syntax(code, lang, theme="monokai", line_numbers=False, word_wrap=False),
                    )
                    sys.stdout.flush()
                    return
                except Exception:  # noqa: BLE001
                    pass
            self._console.print(Text(code))
            sys.stdout.flush()
            return

        text = "\n".join(block.content).strip()
        if not text:
            return
        try:
            self._console.print(Markdown(text))
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            self._console.print(Text(text))
            sys.stdout.flush()


# ── Model Registration ──────────────────────────────────


_SHORT_CHAT_ALIASES = {
    "gpt-oss:20b": "20b",
    "gpt-oss:120b": "120b",
}

_SHORT_EMBED_ALIASES = {
    "bge-m3:567m": "bge",
    "Nomic-embed-text:v1.5": "nomic",
    "embeddinggemma:300m": "gemma",
}


@llm.hookimpl
def register_models(register: Callable) -> None:
    for loc_name, loc in LOCATIONS.items():
        for m in _load_models(loc_name):
            mid = m["id"]
            safe = m.get("safe_id", mid.replace(":", "-"))
            short = _SHORT_CHAT_ALIASES.get(mid) if loc_name == "rzob" else None
            aliases = [safe] + ([short] if short else [])
            register(
                RzobModel(f"fcio-{loc_name}/{safe}", mid, loc),
                aliases=aliases,
            )


@llm.hookimpl
def register_embedding_models(register: Callable) -> None:
    embed_keywords = ("embed", "bge", "gemma")
    for loc_name, loc in LOCATIONS.items():
        for m in _load_models(loc_name):
            mid = m["id"]
            if not any(k in mid.lower() for k in embed_keywords):
                continue
            safe = m.get("safe_id", mid.replace(":", "-"))
            short = _SHORT_EMBED_ALIASES.get(mid) if loc_name == "rzob" else None
            aliases = [safe] + ([short] if short else [])
            register(
                RzobEmbeddingModel(f"fcio-{loc_name}/{safe}", mid, loc),
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
def register_commands(cli: click.Group) -> None:

    @cli.group()
    @click.option(
        "-l",
        "--location",
        "loc_name",
        default=DEFAULT_LOCATION,
        type=click.Choice(list(LOCATIONS.keys())),
        help="FCIO location (default: rzob)",
    )
    @click.pass_context
    def fcio(ctx: click.Context, loc_name: str) -> None:
        "Commands for the FCIO AI platform"
        ctx.ensure_object(dict)
        ctx.obj["location"] = LOCATIONS[loc_name]

    # ── refresh ────────────────────────────────────────

    @fcio.command()
    @click.pass_context
    def refresh(ctx: click.Context) -> None:
        """Fetch available models from API and cache locally"""
        loc: Location = ctx.obj["location"]
        key = get_api_key(loc)
        resp = api_request("GET", "/models", key, loc.api_base)
        raw = resp.json()
        data = raw.get("data", raw if isinstance(raw, list) else [])
        models = []
        for m in data:
            mid = m["id"] if isinstance(m, dict) else str(m)
            models.append(
                {
                    "id": mid,
                    "safe_id": mid.replace(":", "-").replace(".", "_"),
                },
            )
        _cache_path(loc.name).write_text(json.dumps(models, indent=2))
        click.echo(f"Cached {len(models)} models for {loc.name}", err=True)

    # ── models ─────────────────────────────────────────

    @fcio.command("models")
    @click.option("--json", "as_json", is_flag=True, help="Output as raw JSON")
    @click.option("--filter", "filt", help="Filter models by name substring")
    @click.pass_context
    def cmd_models(ctx: click.Context, as_json: bool, filt: str | None) -> None:
        """List available models from the API"""
        loc: Location = ctx.obj["location"]
        key = get_api_key(loc)
        resp = api_request("GET", "/models", key, loc.api_base)
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
                    "embed" if any(k in mid.lower() for k in ("embed", "bge", "gemma")) else "chat"
                )
                click.echo(f"{mtype:>10}  {mid}")

    # ── chat ───────────────────────────────────────────

    @fcio.command("chat")
    @click.argument("prompt", nargs=-1, required=False)
    @click.option("-m", "--model", "model_id", default="gpt-oss:20b", help="Model ID")
    @click.option("-s", "--system", help="System prompt")
    @click.option("-t", "--temperature", type=float, default=0.7, help="Temperature")
    @click.option("--max-tokens", type=int, help="Max tokens")
    @click.option("--stream/--no-stream", default=True, help="Stream response")
    @click.option("--json", "as_json", is_flag=True, help="Output full JSON response")
    @click.option("-i", "--interactive", is_flag=True, help="Interactive chat mode")
    @click.option(
        "--markdown/--no-markdown",
        default=None,
        help="Rich markdown rendering (default: auto-detect from terminal)",
    )
    @click.pass_context
    def cmd_chat(
        ctx: click.Context,
        prompt: tuple[str],
        model_id: str,
        system: str | None,
        temperature: float,
        max_tokens: int | None,
        stream: bool,
        as_json: bool,
        interactive: bool,
        markdown: bool | None,
    ) -> None:
        """Chat with a model, with optional Rich markdown rendering"""
        loc: Location = ctx.obj["location"]
        key = get_api_key(loc)
        model_id = _resolve_model(model_id, key, loc.api_base)

        # Auto-detect: render when stdout is a terminal (not a pipe)
        do_render = markdown if markdown is not None else sys.stdout.isatty()

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
                _send_chat_request(
                    key, body, stream, as_json, render=do_render, api_base=loc.api_base
                )
                messages.append({"role": "assistant", "content": "[...]"})
        else:
            if not prompt_text:
                raise click.ClickException("Prompt required (or use --interactive)")
            messages.append({"role": "user", "content": prompt_text})
            body = _build_chat_body(model_id, messages, temperature, max_tokens)
            _send_chat_request(key, body, stream, as_json, render=do_render, api_base=loc.api_base)

    # ── embed ──────────────────────────────────────────

    @fcio.command("embed")
    @click.argument("model_id")
    @click.argument("text", nargs=-1, required=True)
    @click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
    @click.option("-d", "--dimensions", type=int, help="Output dimension")
    @click.pass_context
    def cmd_embed(
        ctx: click.Context,
        model_id: str,
        text: tuple[str],
        as_json: bool,
        dimensions: int | None,
    ) -> None:
        """Test embedding generation"""
        loc: Location = ctx.obj["location"]
        key = get_api_key(loc)

        body = {
            "model": model_id,
            "input": list(text) if len(text) > 1 else text[0],
        }
        if dimensions:
            body["dimensions"] = dimensions

        resp = api_request("POST", "/embeddings", key, loc.api_base, json_data=body)
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

    @fcio.command("health")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON")
    @click.pass_context
    def cmd_health(ctx: click.Context, as_json: bool) -> None:
        """Check API health and auth"""
        loc: Location = ctx.obj["location"]
        key = get_api_key(loc)

        results = {}

        # Auth test via /models
        try:
            resp = api_request("GET", "/models", key, loc.api_base)
            results["auth"] = "✅ valid"
            results["models_count"] = len(resp.json().get("data", []))
        except ApiError as e:
            results["auth"] = f"❌ {e}"
            results["models_count"] = None

        # Base URL reachable
        try:
            r = httpx.get(loc.api_base.replace("/v1", ""), timeout=5)
            results["base_url"] = f"✅ {r.status_code}"
        except httpx.HTTPError as e:
            results["base_url"] = f"❌ {e}"

        # Chat endpoint smoke test
        try:
            body = {"model": "test", "messages": [{"role": "user", "content": "."}]}
            resp = api_request("POST", "/chat/completions", key, loc.api_base, json_data=body)
            if resp.status_code == httpx.codes.UNAUTHORIZED:
                results["chat_endpoint"] = "❌ auth failed"
            elif resp.status_code == httpx.codes.NOT_FOUND:
                results["chat_endpoint"] = "⚠️  endpoint not found"
            else:
                results["chat_endpoint"] = f"✅ {resp.status_code}"
        except ApiError as e:
            if e.status_code == httpx.codes.BAD_REQUEST and "model" in str(e).lower():
                results["chat_endpoint"] = "✅ reachable (model error expected)"
            else:
                results["chat_endpoint"] = f"❌ {e}"

        if as_json:
            click.echo(json.dumps(results, indent=2))
        else:
            click.echo(f"🔍 FCIO {loc.name.upper()} API Health")
            click.echo("-" * 40)
            for k, v in results.items():
                click.echo(f"{k:<20} {v}")

    # ── simulate ────────────────────────────────────────

    @fcio.command("simulate")
    @click.option(
        "--speed",
        type=click.Choice(["fast", "normal", "slow"]),
        default="normal",
        help="Streaming speed (default: normal)",
    )
    @click.option("--seed", type=int, default=42, help="Random seed for reproducibility")
    @click.option("--raw", is_flag=True, help="Output raw markdown (no Rich rendering)")
    def cmd_simulate(speed: str, seed: int, raw: bool) -> None:
        """Stream a simulated LLM response with Rich markdown rendering.

        Produces token-by-token markdown output that looks like a real
        model response. Blocks are rendered as they finalize (paragraph,
        code fence, list). Use --raw for unformatted pipe output.
        """
        import random
        import time

        rng = random.Random(seed)

        speeds = {"fast": (8, 3, 3, 8), "normal": (25, 12, 1, 4), "slow": (60, 25, 1, 2)}
        delay_ms, jitter_ms, chunk_min, chunk_max = speeds[speed]

        response = (
            "# Python Decorators\n"
            "\n"
            "Decorators are one of Python's most powerful features. They allow you to "
            "**modify** or *extend* the behavior of callable objects without permanently "
            "modifying them.\n"
            "\n"
            "## Basic Syntax\n"
            "\n"
            "A decorator takes a function and returns a modified version:\n"
            "\n"
            "```python\n"
            "def my_decorator(func):\n"
            "    def wrapper(*args, **kwargs):\n"
            '        print("Before call")\n'
            "        result = func(*args, **kwargs)\n"
            '        print("After call")\n'
            "        return result\n"
            "    return wrapper\n"
            "\n"
            "@my_decorator\n"
            "def greet(name):\n"
            '    print(f"Hello, {name}!")\n'
            "\n"
            "greet('World')\n"
            "```\n"
            "\n"
            "## Key Points\n"
            "\n"
            "Important things to remember:\n"
            "\n"
            "- Decorators accept a function and return a new one\n"
            "- The `@decorator` syntax is sugar for `func = decorator(func)`\n"
            "- Use `functools.wraps` to preserve function metadata\n"
            "- Multiple decorators apply bottom-up\n"
            "\n"
            "## Common Use Cases\n"
            "\n"
            "| Pattern | Decorator |\n"
            "|---------|----------|\n"
            "| Logging | `@log_calls` |\n"
            "| Caching | `@lru_cache` |\n"
            "| Auth | `@require_login` |\n"
            "| Retry | `@retry(n=3)` |\n"
            "\n"
            "### Stacking Decorators\n"
            "\n"
            "```bash\n"
            "@decorator_a\n"
            "@decorator_b\n"
            "def my_func():\n"
            "    pass\n"
            "# Same as: my_func = decorator_a(decorator_b(my_func))\n"
            "```\n"
            "\n"
            "> Decorators are just functions that return functions. "
            "Once you grasp that, everything else falls into place.\n"
            "\n"
            "See [PEP 318](https://peps.python.org/pep-0318/) for the full specification. "
            "Happy decorating!\n"
        )

        pos = 0
        renderer = _StreamingRenderer() if not raw else None

        while pos < len(response):
            chunk_size = rng.randint(chunk_min, chunk_max)
            chunk = response[pos : pos + chunk_size]
            pos += chunk_size

            if raw:
                click.echo(chunk, nl=False)
                click.get_text_stream("stdout").flush()
            else:
                renderer.feed(chunk)

            sleep_s = (delay_ms + rng.randint(-jitter_ms, jitter_ms)) / 1000.0
            time.sleep(max(0.0, sleep_s))

        if not raw:
            renderer.flush()

    # ── tokens ─────────────────────────────────────────

    @fcio.command("tokens")
    @click.argument("model_id")
    @click.argument("text", nargs=-1, required=True)
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON")
    @click.pass_context
    def cmd_tokens(ctx: click.Context, model_id: str, text: tuple[str], as_json: bool) -> None:
        """Estimate token count for text (if endpoint supports it)"""
        loc: Location = ctx.obj["location"]
        key = get_api_key(loc)

        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": " ".join(text)}],
            "max_tokens": 1,
        }

        try:
            resp = api_request("POST", "/chat/completions", key, loc.api_base, json_data=body)
            data = resp.json()
            usage = data.get("usage", {})

            if as_json:
                click.echo(json.dumps(usage, indent=2))
            else:
                click.echo(f"Model: {model_id}")
                click.echo(f"Prompt tokens:     {usage.get('prompt_tokens', '?')}")
                click.echo(f"Completion tokens: {usage.get('completion_tokens', '?')}")
                click.echo(f"Total:             {usage.get('total_tokens', '?')}")
        except ApiError as e:
            click.echo(f"⚠️  Token endpoint not supported: {e}", err=True)
            total_chars = sum(len(t) for t in text)
            click.echo(f"Rough estimate: ~{total_chars // 4} tokens (heuristic)")

    # ── ingest ─────────────────────────────────────────

    @fcio.command("ingest")
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
        "--chunk-size",
        type=int,
        default=30,
        help="Lines per chunk (default: 30)",
    )
    @click.option(
        "--overlap",
        type=int,
        default=5,
        help="Overlap lines between chunks (default: 5)",
    )
    @click.option(
        "--yes",
        "skip_confirm",
        is_flag=True,
        help="Skip confirmation preview",
    )
    @click.pass_context
    def cmd_ingest(
        ctx: click.Context,
        collection: str,
        paths: tuple[str, ...],
        glob_pattern: str,
        model_id: str,
        chunk_size: int,
        overlap: int,
        skip_confirm: bool,
    ) -> None:
        """Ingest files into an llm embedding collection.

        COLLECTION is the collection name. PATHS are directories (recursive
        discovery) or explicit files.

        \b
        Examples:
          llm fcio ingest mydocs ./docs/
          llm fcio ingest mydocs ./docs/ --glob '*.py'
          llm fcio ingest mydocs file1.md file2.md
          llm fcio ingest mydocs ./src/ -m bge --chunk-size 50 --overlap 10
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

        with Progress(
            SpinnerColumn(),
            TextColumn("{task.fields[filename]}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task(
                "ingest",
                total=total_chunks,
                filename="Starting...",
            )

            def _tracked() -> Iterator[tuple[str, str]]:
                for name, chunks in file_chunks.items():
                    fname = Path(name).name
                    for cid, text in chunks:
                        progress.update(task, filename=fname)
                        yield cid, text
                        progress.advance(task)

            col.embed_multi(_tracked(), store=True)

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


def _send_chat_request(
    key: str,
    body: dict,
    stream: bool,
    as_json: bool,
    render: bool = False,
    api_base: str = "",
) -> None:
    if stream:
        body["stream"] = True
        try:
            renderer = _StreamingRenderer() if render else None
            with httpx.Client() as client:
                url = f"{api_base}/chat/completions"
                sse_headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                }
                for content in _iter_sse_content(client, url, sse_headers, body):
                    if render:
                        renderer.feed(content)
                    else:
                        click.echo(content, nl=False)
            if render:
                renderer.flush()
            else:
                click.echo()
        except ApiError:
            raise
        except httpx.HTTPError as e:
            raise ApiError(f"Streaming error: {e}") from e
    else:
        resp = api_request("POST", "/chat/completions", key, api_base, json_data=body)
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise ApiError("Empty response from API - no choices returned")
        if as_json:
            click.echo(json.dumps(data, indent=2))
        else:
            content = choices[0]["message"].get("content", "")
            click.echo(content)
            if "usage" in data:
                u = data["usage"]
                click.echo(
                    f"\n⚡ {u.get('prompt_tokens', '?')}→{u.get('completion_tokens', '?')} tokens",
                    err=True,
                )
