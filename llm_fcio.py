"""llm-fcio: Plugin für FCIO AI platform (multi-location)"""

import contextlib
import json
import random
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click
import httpx
import llm
import pathspec
import sqlite_utils
from httpx_sse import connect_sse
from pydantic import Field
from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text

_VERBOSE: bool = False
_DEBUG: bool = False
_debug_console = Console(stderr=True, force_terminal=True)


_B32C = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _b32c_encode(value: int, length: int) -> str:
    """Encode integer as base32-crockford string of given length."""
    chars: list[str] = []
    for _ in range(length):
        chars.append(_B32C[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def _generate_lid() -> str:
    """Generate a LID: 42-bit ms timestamp + 22-bit random, base32-crockford."""
    ts_ms = int(time.time() * 1000) & ((1 << 42) - 1)
    rand = random.getrandbits(22)
    combined = (ts_ms << 22) | rand
    # 64 bits / 5 bits per char = 13 chars, split as XXXXXXXXX-XXXX
    encoded = _b32c_encode(combined, 13)
    return f"{encoded[:9]}-{encoded[9:]}"


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


def _mask_auth_header(name: str, value: str) -> str:
    """Mask Authorization header values, pass through all others."""
    if name.lower() == "authorization":
        return "Bearer sk-***..."
    return value


def _log_request_body(content: bytes) -> None:
    """Log request body: JSON pretty-print or raw with truncation."""
    try:
        body = json.loads(content)
        pretty = json.dumps(body, indent=2)
        _debug_console.print(Syntax(pretty, "json", theme="monokai", line_numbers=False))
    except json.JSONDecodeError, UnicodeDecodeError:
        raw = content.decode("utf-8", errors="replace")
        if len(raw) > 500:
            raw = raw[:500] + "..."
        _debug_console.print(raw)


def _log_response_body(response: httpx.Response) -> None:
    """Log response body: SSE skip, JSON pretty-print, or raw with truncation."""
    ct = response.headers.get("content-type", "")
    if "text/event-stream" in ct:
        _debug_console.print("  [dim]Response: SSE stream[/dim]")
        return
    try:
        body_text = response.text
        if not body_text:
            return
        try:
            body_json = json.loads(body_text)
            pretty = json.dumps(body_json, indent=2)
            if len(pretty) > 2000:
                pretty = pretty[:2000] + "\n... (truncated)"
            _debug_console.print(Syntax(pretty, "json", theme="monokai", line_numbers=False))
        except json.JSONDecodeError, UnicodeDecodeError:
            if len(body_text) > 500:
                body_text = body_text[:500] + "... (truncated)"
            _debug_console.print(body_text)
    except ValueError, OSError, RuntimeError:
        _debug_console.print("  [dim](body not available)[/dim]")


def _make_client(
    *, verbose: bool = False, debug: bool = False, timeout: float = 30.0
) -> httpx.Client:
    """Create httpx.Client with optional verbose logging and/or debug header."""
    if not verbose and not debug:
        return httpx.Client(timeout=timeout)

    debug_id = _generate_lid() if debug else None

    def _on_request(request: httpx.Request) -> None:
        if debug_id:
            request.headers["X-Skvaider-Debug-ID"] = debug_id
        if verbose:
            _debug_console.print(
                f"[bold blue]\u2192[/bold blue] {request.method} {request.url.path}"
            )
            for name, value in request.headers.items():
                _debug_console.print(f"  [dim]{name}:[/dim] {_mask_auth_header(name, value)}")
            if request.content:
                _log_request_body(request.content)

    def _on_response(response: httpx.Response) -> None:
        if verbose:
            _debug_console.print(
                f"[bold green]\u2190[/bold green] {response.status_code} {response.reason_phrase}"
            )
            for name, value in response.headers.items():
                _debug_console.print(f"  [dim]{name}:[/dim] {value}")
            _log_response_body(response)

    return httpx.Client(
        timeout=timeout,
        event_hooks={"request": [_on_request], "response": [_on_response]},
    )


def _auth_headers(key: str) -> dict[str, str]:
    """Build Authorization Bearer + JSON content-type headers."""
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _build_messages(
    prompt: llm.Prompt,
    conversation: llm.Conversation | None,
) -> list[dict]:
    """Build the messages list from prompt and conversation history."""
    messages: list[dict] = []
    if prompt.system:
        messages.append({"role": "system", "content": prompt.system})
    if conversation:
        for r in conversation.responses:
            if r.prompt.system:
                messages.append({"role": "system", "content": r.prompt.system})
            if r.prompt.prompt:
                messages.append({"role": "user", "content": r.prompt.prompt})
            messages.append({"role": "assistant", "content": r.text_or_raise()})  # ty: ignore[unresolved-attribute]
    # Build user message with attachments
    user_content_parts: list[dict] = []
    for att in prompt.attachments or []:
        att_type = att.resolve_type()
        if att_type == "text/plain":
            text = att.content_bytes().decode("utf-8")
            user_content_parts.append({"type": "text", "text": text})
    if prompt.prompt:
        user_content_parts.append({"type": "text", "text": prompt.prompt})
    if user_content_parts:
        content = (
            user_content_parts[0]["text"] if len(user_content_parts) == 1 else user_content_parts
        )
        messages.append({"role": "user", "content": content})
    return messages


def _extract_content(data: dict) -> str:
    """Extract content from API response data, raising on empty/missing choices."""
    choices = data.get("choices") or []
    if not choices:
        raise ApiError("Empty response from API - no choices returned")
    msg = choices[0].get("message") or {}
    return msg.get("content") or ""


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
    headers = _auth_headers(key)
    headers["Accept"] = "application/json"

    with _make_client(verbose=_VERBOSE, debug=_DEBUG, timeout=30.0) as client:
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


@dataclass(slots=True)
class _SSEMetadata:
    """Accumulated metadata from SSE streaming events."""

    finish_reason: str | None = None
    usage: dict | None = None


def _apply_usage(response: llm.Response, usage: dict | None) -> None:
    """Set token usage on the response object from API usage dict."""
    if not usage:
        return
    response.set_usage(
        input=usage.get("prompt_tokens"),
        output=usage.get("completion_tokens"),
        details={k: v for k, v in usage.items() if k not in ("prompt_tokens", "completion_tokens")},
    )


def _iter_sse_content(
    client: httpx.Client,
    url: str,
    headers: dict,
    body: dict,
) -> tuple[_SSEMetadata, Iterator[str]]:
    """Yield content deltas from SSE streaming response and collect metadata.

    Returns a tuple of (metadata, content_iterator). The metadata is
    populated as the iterator is consumed — read it after iteration completes.
    """
    meta = _SSEMetadata()

    def _generate() -> Iterator[str]:
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
                except json.JSONDecodeError:
                    continue
                # Collect usage from top-level event (final chunk)
                if "usage" in event and event["usage"]:
                    meta.usage = event["usage"]
                choices = event.get("choices", [])
                if not choices:
                    continue
                choice = choices[0]
                # Collect finish_reason
                if choice.get("finish_reason"):
                    meta.finish_reason = choice["finish_reason"]
                delta = choice.get("delta", {})
                if delta.get("content"):
                    yield delta["content"]

    return meta, _generate()


# ── Hard-coded Models ──────────────────────────────────

# Bekannte Modelle. Ändern sich selten — bei neuen Versionen reicht
# `llm fcio refresh` um den API-Cache zu updaten.
_HARD_CODED_MODELS: list[dict] = [
    {"id": "gpt-oss:20b", "safe_id": "gpt-oss-20b"},
    {"id": "gpt-oss:120b", "safe_id": "gpt-oss-120b"},
    {"id": "bge-m3:567m", "safe_id": "bge-m3-567m"},
    {"id": "Nomic-embed-text:v1.5", "safe_id": "nomic-embed-text-v1_5"},
    {"id": "embeddinggemma:300m", "safe_id": "embeddinggemma-300m"},
]


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
    return json.loads(p.read_text())


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
    supports_schema = True
    attachment_types = {"text/plain"}  # noqa: RUF012

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
        return f"Flying Circus: {self.model_id}"

    def execute(
        self,
        prompt: llm.Prompt,
        stream: bool,
        response: llm.Response,
        conversation: llm.Conversation | None,
        key: str | None,
    ) -> Iterator[str]:
        messages = _build_messages(prompt, conversation)

        body: dict[str, Any] = {"model": self.api_id, "messages": messages}
        if prompt.options.temperature is not None:  # ty: ignore[unresolved-attribute]
            body["temperature"] = prompt.options.temperature  # ty: ignore[unresolved-attribute]
        if prompt.options.max_tokens is not None:  # ty: ignore[unresolved-attribute]
            body["max_tokens"] = prompt.options.max_tokens  # ty: ignore[unresolved-attribute]
        if prompt.options.top_p is not None:  # ty: ignore[unresolved-attribute]
            body["top_p"] = prompt.options.top_p  # ty: ignore[unresolved-attribute]
        if prompt.options.tools is not None:  # ty: ignore[unresolved-attribute]
            body["tools"] = prompt.options.tools  # ty: ignore[unresolved-attribute]
        if prompt.options.response_format is not None:  # ty: ignore[unresolved-attribute]
            body["response_format"] = prompt.options.response_format  # ty: ignore[unresolved-attribute]
        if prompt.schema:  # ty: ignore[unresolved-attribute]
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": prompt.schema},
            }

        if key is None:
            key = self.get_key()
        if key is None:
            raise ValueError("API key required — set via 'llm keys set fcio-rzob'")
        headers = _auth_headers(key)

        api_base = self._location.api_base
        if stream:
            body["stream"] = True
            with _make_client(verbose=_VERBOSE, debug=_DEBUG) as client:
                url = f"{api_base}/chat/completions"
                meta, content_iter = _iter_sse_content(client, url, headers, body)
                yield from content_iter
            # Populate response metadata after iteration completes
            response.response_json = {
                "finish_reason": meta.finish_reason,
                "usage": meta.usage or {},
            }
            _apply_usage(response, meta.usage)
        else:
            with _make_client(verbose=_VERBOSE, debug=_DEBUG) as client:
                resp = client.post(
                    f"{api_base}/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=None,
                )
                resp.raise_for_status()
                data = resp.json()
                content = _extract_content(data)
                yield content
                response.response_json = data
                _apply_usage(response, data.get("usage"))


# ── Embedding Model ──────────────────────────────────────


class RzobEmbeddingModel(llm.EmbeddingModel):
    batch_size = 100

    def __init__(self, model_id: str, api_id: str, location: Location) -> None:
        self.model_id = model_id
        self.api_id = api_id
        self.needs_key = location.key_name
        self.key_env_var = location.env_var
        self._location = location

    def embed_batch(self, items: Iterable[str | bytes]) -> Iterator[list[float]]:
        key = self.get_key()
        if key is None:
            raise ValueError("API key required — set via 'llm keys set fcio-rzob'")
        api_base = self._location.api_base
        with _make_client(verbose=_VERBOSE, debug=_DEBUG, timeout=30.0) as client:
            resp = client.post(
                f"{api_base}/embeddings",
                headers=_auth_headers(key),
                json={"model": self.api_id, "input": list(items)},
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
                except ValueError, OSError, RuntimeError:
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
        except ValueError, OSError, RuntimeError:
            self._console.print(Text(text))
            sys.stdout.flush()


def install_renderer_patch() -> None:
    """Monkey-patch llm.Response.__iter__ for rich streaming markdown output.

    Only activates when stdout is a TTY. Falls back to original behavior
    on renderer failure. Idempotent — safe to call multiple times.

    Spec decisions: renderer-hook, renderer-safety
    """
    if not sys.stdout.isatty():
        return

    original_iter = llm.Response.__iter__

    # Idempotency: don't double-wrap
    if getattr(original_iter, "_fcio_patched", False):
        return

    def _patched_iter(self: llm.Response) -> Iterator[str]:
        try:
            renderer = _StreamingRenderer()
        except Exception:  # noqa: BLE001 — renderer-safety: fallback on any failure
            yield from original_iter(self)
            return
        use_renderer = True
        for chunk in original_iter(self):
            if use_renderer:
                try:
                    renderer.feed(chunk)
                except Exception:  # noqa: BLE001 — renderer-safety: graceful degradation
                    use_renderer = False
            yield chunk
        if use_renderer:
            with contextlib.suppress(Exception):  # noqa: BLE001 — renderer-safety
                renderer.flush()

    _patched_iter._fcio_patched = True  # ty: ignore[unresolved-attribute]
    llm.Response.__iter__ = _patched_iter  # ty: ignore[invalid-assignment]


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
    chat_keywords = ("gpt", "llama", "qwen", "mistral", "chat", "claude", "deepseek")
    for loc_name, loc in LOCATIONS.items():
        models = _load_models(loc_name) or _HARD_CODED_MODELS
        for m in models:
            mid = m["id"]
            if not any(k in mid.lower() for k in chat_keywords):
                continue
            safe = m["safe_id"]
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
        models = _load_models(loc_name) or _HARD_CODED_MODELS
        for m in models:
            mid = m["id"]
            if not any(k in mid.lower() for k in embed_keywords):
                continue
            safe = m["safe_id"]
            short = _SHORT_EMBED_ALIASES.get(mid) if loc_name == "rzob" else None
            aliases = [safe] + ([short] if short else [])
            register(
                RzobEmbeddingModel(f"fcio-{loc_name}/{safe}", mid, loc),
                aliases=aliases,
            )


# ── Template System ────────────────────────────────────


TEMPLATES = {
    "review": (
        "You are a senior code reviewer. Analyze the provided code files for bugs, "
        "security vulnerabilities, performance issues, and maintainability problems. "
        "For each issue found, specify the file, line range, and severity (critical/high/medium/low). "
        "Suggest concrete fixes. Also highlight positive patterns and well-written code. "
        "Structure your review by category: correctness, security, performance, readability."
    ),
    "overview": (
        "You are a software architect providing a project overview. Analyze the provided "
        "code files and describe: the project's purpose and domain, the overall architecture "
        "and key components, the technology stack and dependencies, the code organization "
        "and module structure, entry points and main flows. Identify architectural strengths "
        "and areas for improvement. Be concise and focus on the big picture."
    ),
}


def fcio_template_loader() -> dict[str, llm.Template]:
    """Return fcio templates registered via llm's template loader hook."""
    return {name: llm.Template(name=name, system=prompt) for name, prompt in TEMPLATES.items()}


@llm.hookimpl
def register_template_loaders(register: Callable) -> None:
    register("fcio", fcio_template_loader)


# ── Ingest Helpers ──────────────────────────────────────


_HARD_EXCLUDES = [
    "venv/",
    ".venv/",
    "node_modules/",
    "__pycache__/",
    ".git/",
    "*.egg-info/",
]

_CODE_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".rb",
        ".php",
        ".sh",
        ".bash",
        ".sql",
        ".html",
        ".css",
        ".scss",
        ".toml",
        ".yaml",
        ".yml",
        ".md",
        ".rst",
    }
)


def _discover_files(
    paths: tuple[Path, ...],
    glob_pattern: str,
) -> list[Path]:
    """Discover files from paths, applying gitignore + hard-exclude filtering."""
    all_files: list[Path] = []
    for p in paths:
        resolved = p.resolve()
        if resolved.is_file():
            all_files.append(resolved)
        elif resolved.is_dir():
            gitignore_path = resolved / ".gitignore"
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


def collect_code_files(directory: Path) -> list[Path]:
    """Collect code files from directory using extension whitelist and .gitignore filtering."""
    resolved = directory.resolve()
    if not resolved.is_dir():
        return []
    gitignore_path = resolved / ".gitignore"
    spec_lines: list[str] = []
    if gitignore_path.exists():
        spec_lines = gitignore_path.read_text().splitlines()
    spec_lines.extend(_HARD_EXCLUDES)
    spec = pathspec.PathSpec.from_lines("gitwildmatch", spec_lines)

    all_files: list[Path] = []
    for candidate in sorted(resolved.rglob("*")):
        if not candidate.is_file():
            continue
        if candidate.suffix not in _CODE_EXTENSIONS:
            continue
        rel = candidate.relative_to(resolved)
        if not spec.match_file(str(rel)):
            all_files.append(candidate)
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
    if chunk_size <= 0:
        msg = "chunk_size must be positive"
        raise ValueError(msg)
    if overlap < 0:
        msg = "overlap must be non-negative"
        raise ValueError(msg)
    if overlap >= chunk_size:
        msg = "overlap must be less than chunk_size"
        raise ValueError(msg)
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


# ── Public API ──────────────────────────────────────────


def refresh_models(loc_name: str = DEFAULT_LOCATION) -> list[dict]:
    """Fetch available models from API and cache locally.

    Returns list of model dicts with 'id' and 'safe_id' keys.
    """
    loc = LOCATIONS[loc_name]
    key = get_api_key(loc)
    resp = api_request("GET", "/models", key, loc.api_base)
    raw = resp.json()
    data = raw.get("data", raw if isinstance(raw, list) else [])
    models: list[dict] = []
    for m in data:
        mid = m["id"] if isinstance(m, dict) else str(m)
        models.append(
            {
                "id": mid,
                "safe_id": mid.replace(":", "-").replace(".", "_"),
            },
        )
    _cache_path(loc.name).write_text(json.dumps(models, indent=2))
    return models


def list_models(
    loc_name: str = DEFAULT_LOCATION,
    filter: str | None = None,
) -> list[dict]:
    """List available models from API.

    Returns raw model data from the API. Optional substring filter
    applied client-side.
    """
    loc = LOCATIONS[loc_name]
    key = get_api_key(loc)
    resp = api_request("GET", "/models", key, loc.api_base)
    models = resp.json().get("data", [])
    if filter:
        models = [m for m in models if filter.lower() in m.get("id", "").lower()]
    return models


def get_model_info(model_id: str, loc_name: str = DEFAULT_LOCATION) -> dict:
    """Show details for a specific model.

    Raises ModelError on 404.
    """
    loc = LOCATIONS[loc_name]
    key = get_api_key(loc)
    try:
        resp = api_request("GET", f"/models/{model_id}", key, loc.api_base)
    except ApiError as e:
        if e.status_code == 404:
            raise ModelError(f"Model not found: {model_id}") from e
        raise
    return resp.json().get("data", resp.json())


def get_cached_models(loc_name: str = DEFAULT_LOCATION) -> list[dict]:
    """Read cached models without calling the API."""
    return _load_models(loc_name)


def get_capabilities(loc_name: str = DEFAULT_LOCATION) -> dict:
    """Probe endpoint capabilities and return structured result.

    Returns dict with ``endpoint``, ``models`` (with counts),
    and ``features`` (probe status per endpoint).
    """
    loc = LOCATIONS[loc_name]
    key = get_api_key(loc)

    auth_status = "valid"
    models_data: list[dict] = []
    try:
        resp = api_request("GET", "/models", key, loc.api_base)
        models_data = resp.json().get("data", [])
    except ApiError as e:
        auth_status = str(e)

    embed_keywords = ("embed", "bge", "gemma")
    chat_keywords = ("gpt", "llama", "qwen", "mistral", "chat", "claude", "deepseek")
    chat_models: list[dict] = []
    embed_models: list[dict] = []
    other_models: list[dict] = []
    for m in models_data:
        mid = m["id"]
        if any(k in mid.lower() for k in embed_keywords):
            embed_models.append(m)
        elif any(k in mid.lower() for k in chat_keywords):
            chat_models.append(m)
        else:
            other_models.append(m)

    def _probe_endpoint(
        method: str,
        path: str,
        body: dict | None = None,
        model_error_marker: str = "model",
    ) -> str:
        try:
            api_request(method, path, key, loc.api_base, json_data=body)
            return "available"
        except ApiError as e:
            if e.status_code and model_error_marker in str(e).lower():
                return "available"
            if e.status_code == httpx.codes.UNAUTHORIZED:
                return "auth failed"
            return str(e)

    chat_status = _probe_endpoint(
        "POST",
        "/chat/completions",
        body={"model": "_probe_test", "messages": [{"role": "user", "content": "."}]},
    )
    streaming_status = _probe_endpoint(
        "POST",
        "/chat/completions",
        body={
            "model": "_probe_test",
            "messages": [{"role": "user", "content": "."}],
            "stream": True,
        },
    )
    embed_status = _probe_endpoint(
        "POST",
        "/embeddings",
        body={"model": "_probe_test", "input": "test"},
    )
    schema_status = _probe_endpoint(
        "POST",
        "/chat/completions",
        body={
            "model": "_probe_test",
            "messages": [{"role": "user", "content": "."}],
            "response_format": {"type": "json_object"},
        },
    )

    return {
        "endpoint": {
            "name": loc.name,
            "api_base": loc.api_base,
            "auth": auth_status,
        },
        "models": {
            "chat": chat_models,
            "embedding": embed_models,
            "other": other_models,
            "counts": {
                "chat": len(chat_models),
                "embedding": len(embed_models),
                "other": len(other_models),
                "total": len(models_data),
            },
        },
        "features": {
            "chat_completions": {
                "status": chat_status,
                "method": "POST",
                "path": "/chat/completions",
            },
            "streaming": {
                "status": streaming_status,
                "method": "POST",
                "path": "/chat/completions",
                "param": "stream",
            },
            "embeddings": {"status": embed_status, "method": "POST", "path": "/embeddings"},
            "schema_output": {
                "status": schema_status,
                "method": "POST",
                "path": "/chat/completions",
                "param": "response_format",
            },
        },
    }


def estimate_tokens(
    text: str,
    model_id: str = "gpt-oss:20b",
    loc_name: str = DEFAULT_LOCATION,
) -> dict:
    """Estimate token count for text via API.

    Falls back to ``len(text) // 4`` heuristic when the endpoint
    does not support tokenisation. The result includes a
    ``_fallback: True`` key in that case.
    """
    loc = LOCATIONS[loc_name]
    key = get_api_key(loc)
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": text}],
        "max_tokens": 1,
    }
    try:
        resp = api_request("POST", "/chat/completions", key, loc.api_base, json_data=body)
        data = resp.json()
        return data.get("usage", {})
    except ApiError:
        total_chars = len(text)
        return {"prompt_tokens": total_chars // 4, "_fallback": True}


def ingest_files(
    collection: str,
    paths: str | Path | list[str | Path],
    *,
    glob: str = "*.md",
    model_id: str = "bge-m3-567m",
    chunk_size: int = 30,
    overlap: int = 5,
    loc_name: str = DEFAULT_LOCATION,
) -> int:
    """Chunk and embed files into an ``llm`` embedding collection.

    Returns total number of chunks ingested.
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    resolved = tuple(Path(p) for p in paths)
    files = _discover_files(resolved, glob)

    if not files:
        return 0

    file_chunks: dict[str, list[tuple[str, str]]] = {}
    for f in files:
        text = f.read_text(errors="replace")
        display_path = str(f)
        chunks = _chunk_lines(text, display_path, chunk_size, overlap)
        if chunks:
            file_chunks[display_path] = chunks

    total_chunks = sum(len(cs) for cs in file_chunks.values())
    if total_chunks == 0:
        return 0

    db = sqlite_utils.Database(llm.user_dir() / "embeddings.db")
    if llm.Collection.exists(db, collection):
        col = llm.Collection(collection, db)
    else:
        col = llm.Collection(collection, db=db, model_id=model_id)

    def _gen() -> Iterator[tuple[str, str]]:
        for _name, chunks in file_chunks.items():
            yield from chunks

    col.embed_multi(_gen(), store=True)
    return total_chunks


def analyze_code(
    analysis_type: str = "review",
    files: list[str] | None = None,
    model_id: str | None = None,
    loc_name: str = DEFAULT_LOCATION,
) -> str:
    """Analyze code files with a review or overview template.

    ``analysis_type`` must be one of ``"review"`` or ``"overview"``.

    When ``files`` is ``None`` the current working directory is
    scanned automatically via :func:`collect_code_files`.

    If ``model_id`` is ``None`` the default FCIO chat model is used.

    Returns the generated analysis text.
    """
    resolved = [Path(f).resolve() for f in files] if files else collect_code_files(Path.cwd())

    if not resolved:
        return ""

    fragments = [llm.Fragment(content=f.read_text(), source=str(f)) for f in resolved]

    if model_id is None:
        model_id = f"fcio-{loc_name}/gpt-oss-20b-20b"
    m = llm.get_model(model_id)

    response = m.prompt(fragments=fragments, system=TEMPLATES[analysis_type])
    return response.text()


# ── Pytest Failure Analyzer ──────────────────────────────


_FOCUS_INSTRUCTIONS: dict[str, str] = {
    "quick": "Provide a brief summary of the test failures.",
    "fix": "Provide code suggestions to fix the test failures.",
    "root-cause": "Perform a deep root-cause analysis of the test failures.",
}


def collect_failures(reports: list[Any]) -> list[dict[str, str]]:
    """Extract failure information from pytest TestReport-like objects.

    Returns a list of dicts with keys: test_name, outcome, message, traceback.
    Only includes reports where outcome is "failed".
    """
    failures: list[dict[str, str]] = []
    for report in reports:
        if report.outcome != "failed":
            continue
        if report.call is not None and report.call.excinfo is not None:
            message = report.call.excinfo.exconly()
        else:
            message = str(report.longrepr)
        failures.append(
            {
                "test_name": report.nodeid,
                "outcome": report.outcome,
                "message": message,
                "traceback": str(report.longrepr),
            }
        )
    return failures


def build_failure_prompt(failures: list[dict[str, str]], focus: str = "quick") -> str:
    """Build an LLM prompt from a list of failure dicts.

    The focus parameter controls the instruction style:
    - "quick": brief summary instruction
    - "fix": include code suggestions instruction
    - "root-cause": deep analysis instruction
    """
    if not failures:
        return "No test failures found."
    instruction = _FOCUS_INSTRUCTIONS.get(focus, _FOCUS_INSTRUCTIONS["quick"])
    parts: list[str] = [instruction, ""]
    for failure in failures:
        parts.append(f"Test: {failure['test_name']}")
        parts.append(f"Message: {failure['message']}")
        parts.append("")
    return "\n".join(parts)


def analyze_failures(
    failures: list[dict[str, str]],
    focus: str = "quick",
    model_id: str | None = None,
    loc_name: str = DEFAULT_LOCATION,
) -> str:
    """Send failure information to an LLM for analysis.

    Uses :func:`build_failure_prompt` to construct the prompt,
    then sends it to the default FCIO chat model.

    Returns the model's response text, or ``""`` if *failures* is empty.
    """
    if not failures:
        return ""

    prompt_text = build_failure_prompt(failures, focus=focus)

    if model_id is None:
        model_id = f"fcio-{loc_name}/gpt-oss-20b-20b"
    m = llm.get_model(model_id)

    response = m.prompt(prompt_text)
    return response.text()


# ── Pytest Plugin Hooks ─────────────────────────────────────


def pytest_addoption(parser: Any) -> None:  # noqa: ANN401
    """Register FCIO failure analyzer CLI options."""
    group = parser.getgroup("fcio", "FCIO Failure Analyzer")
    group.addoption(
        "--fcio-analyze",
        action="store_true",
        default=False,
        help="Enable FCIO LLM-powered test failure analysis",
    )
    group.addoption(
        "--fcio-focus",
        choices=["quick", "fix", "root-cause"],
        default="quick",
        help="Analysis focus: quick, fix, or root-cause (default: quick)",
    )
    group.addoption(
        "--fcio-model",
        default=None,
        help="Override model for failure analysis",
    )


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: Any) -> None:  # noqa: ANN401
    """When --fcio-analyze is active, analyze failures and print results."""
    if not config.getoption("--fcio-analyze"):
        return

    failed_reports = terminalreporter.stats.get("failed", [])
    if not failed_reports:
        return

    failures = collect_failures(failed_reports)
    if not failures:
        return

    focus = config.getoption("--fcio-focus")
    model_id = config.getoption("--fcio-model")

    try:
        analysis = analyze_failures(failures, focus=focus, model_id=model_id)
    except Exception as exc:  # noqa: BLE001 — must not crash the test run
        terminalreporter.write_line("FCIO Failure Analysis: ERROR", bold=True)
        terminalreporter.write_line(f"  {exc}")
        return

    if not analysis:
        return

    terminalreporter.write_line("FCIO Failure Analysis", bold=True)
    terminalreporter.write_line(analysis)


# ── CLI Commands ────────────────────────────────────────


# ── fcio group ──────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.option(
    "-l",
    "--location",
    "loc_name",
    default=DEFAULT_LOCATION,
    type=click.Choice(list(LOCATIONS.keys())),
    help="FCIO location (default: rzob)",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Log raw HTTP requests/responses",
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable server-side debug recording",
)
@click.pass_context
def fcio(ctx: click.Context, loc_name: str, verbose: bool, debug: bool) -> None:
    "Commands for the FCIO AI platform"
    ctx.ensure_object(dict)
    ctx.obj["location"] = LOCATIONS[loc_name]
    global _VERBOSE, _DEBUG
    _VERBOSE = verbose
    _DEBUG = debug
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())

    # ── refresh ────────────────────────────────────────────


@fcio.command()
@click.pass_context
def refresh(ctx: click.Context) -> None:
    """Fetch available models from API and cache locally"""
    loc_name: str = ctx.obj["location"].name
    models = refresh_models(loc_name)
    click.echo(f"Cached {len(models)} models for {loc_name}", err=True)

    # ── models ─────────────────────────────────────────────


@fcio.command("models")
@click.argument("model_id", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as raw JSON")
@click.option("--filter", "filt", help="Filter models by name substring")
@click.pass_context
def cmd_models(ctx: click.Context, model_id: str | None, as_json: bool, filt: str | None) -> None:
    """List available models, or show details for MODEL_ID"""
    loc_name: str = ctx.obj["location"].name

    if model_id:
        try:
            data = get_model_info(model_id, loc_name)
        except ModelError as e:
            raise click.ClickException(str(e)) from e
        if as_json:
            click.echo(json.dumps(data, indent=2))
        else:
            click.echo(f"Model: {data.get('id', model_id)}")
            click.echo(f"ID:     {data.get('id', 'unknown')}")
            click.echo(f"Owner:  {data.get('owned_by', 'unknown')}")
            created = data.get("created")
            click.echo(f"Created: {created or 'unknown'}")
            click.echo("Type:   chat")
        return

    models = list_models(loc_name, filt)
    if as_json:
        click.echo(json.dumps(models, indent=2))
    else:
        click.echo(f"{'Type':>10}  {'ID'}")
        click.echo("-" * 55)
        for m in models:
            mid = m.get("id", "unknown")
            mtype = "embed" if any(k in mid.lower() for k in ("embed", "bge", "gemma")) else "chat"
            click.echo(f"{mtype:>10}  {mid}")

    # ── chat ───────────────────────────────────────────────


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
            _send_chat_request(key, body, stream, as_json, render=do_render, api_base=loc.api_base)
            messages.append({"role": "assistant", "content": "[...]"})
    else:
        if not prompt_text:
            raise click.ClickException("Prompt required (or use --interactive)")
        messages.append({"role": "user", "content": prompt_text})
        body = _build_chat_body(model_id, messages, temperature, max_tokens)
        _send_chat_request(key, body, stream, as_json, render=do_render, api_base=loc.api_base)

    # ── embed ──────────────────────────────────────────────


@fcio.command("embed")
@click.argument("text", nargs=-1, required=True)
@click.option(
    "-m",
    "--model",
    "model_id",
    default="bge-m3-567m",
    help="Embedding model (default: bge-m3-567m)",
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.option("-d", "--dimensions", type=int, help="Output dimension")
@click.pass_context
def cmd_embed(
    ctx: click.Context,
    text: tuple[str],
    model_id: str,
    as_json: bool,
    dimensions: int | None,
) -> None:
    """Test embedding generation"""
    loc: Location = ctx.obj["location"]
    key = get_api_key(loc)

    body: dict[str, Any] = {
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


@fcio.command("capabilities")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def cmd_capabilities(ctx: click.Context, as_json: bool) -> None:
    """Show endpoint capabilities and available models"""
    loc_name: str = ctx.obj["location"].name
    result = get_capabilities(loc_name)

    if as_json:
        click.echo(json.dumps(result, indent=2))
        return

    ep = result["endpoint"]
    click.echo(f"🔍 FCIO {ep['name'].upper()} Capabilities")
    click.echo("=" * 50)
    click.echo()
    click.echo("Endpoint:")
    click.echo(f"  Name:        {ep['name']}")
    click.echo(f"  API Base:    {ep['api_base']}")
    auth_icon = "✅" if ep["auth"] == "valid" else "❌"
    click.echo(f"  Auth:        {auth_icon} {ep['auth']}")
    click.echo()

    def _print_models(label: str, models: list[dict]) -> None:
        click.echo(f"{label} ({len(models)}):")
        if not models:
            click.echo("  (none)")
        for m in models:
            meta_parts: list[str] = []
            if owned := m.get("owned_by"):
                meta_parts.append(f"owned: {owned}")
            if created := m.get("created"):
                meta_parts.append(f"created: {created}")
            meta = f"  [{', '.join(meta_parts)}]" if meta_parts else ""
            click.echo(f"  - {m['id']}{meta}")
        click.echo()

    _print_models("Chat Models", result["models"]["chat"])
    _print_models("Embedding Models", result["models"]["embedding"])
    _print_models("Other Models", result["models"]["other"])

    def _status_icon(s: str) -> str:
        if s == "available":
            return "✅ available"
        if s == "auth failed":
            return "❌ auth failed"
        return f"❌ {s}"

    feats = result["features"]
    click.echo("Features:")
    click.echo(
        f"  Chat completions:  {_status_icon(feats['chat_completions']['status'])} (POST /chat/completions)"
    )
    click.echo(
        f"  Streaming:         {_status_icon(feats['streaming']['status'])} (POST /chat/completions, stream)"
    )
    click.echo(
        f"  Schema output:     {_status_icon(feats['schema_output']['status'])} (POST /chat/completions, response_format)"
    )
    click.echo(
        f"  Embeddings:        {_status_icon(feats['embeddings']['status'])} (POST /embeddings)"
    )

    # ── simulate ────────────────────────────────────────────


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
        elif renderer is not None:
            renderer.feed(chunk)

        sleep_s = (delay_ms + rng.randint(-jitter_ms, jitter_ms)) / 1000.0
        time.sleep(max(0.0, sleep_s))

    if not raw and renderer is not None:
        renderer.flush()

    # ── tokens ─────────────────────────────────────────────


@fcio.command("tokens")
@click.argument("text", nargs=-1, required=True)
@click.option("-m", "--model", "model_id", default="gpt-oss:20b", help="Model ID")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def cmd_tokens(ctx: click.Context, text: tuple[str], model_id: str, as_json: bool) -> None:
    """Estimate token count for text (if endpoint supports it)"""
    loc_name: str = ctx.obj["location"].name
    result = estimate_tokens(" ".join(text), model_id, loc_name)

    if as_json:
        click.echo(json.dumps(result, indent=2))
    elif result.get("_fallback"):
        click.echo("\u26a0\ufe0f  Token endpoint not supported, using heuristic", err=True)
        click.echo(f"Rough estimate: ~{result.get('prompt_tokens', '?')} tokens (heuristic)")
    else:
        click.echo(f"Model: {model_id}")
        click.echo(f"Tokens: {result.get('prompt_tokens', '?')}")

    # ── ingest ─────────────────────────────────────────────


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
    loc_name: str = ctx.obj["location"].name

    resolved_paths = tuple(Path(p) for p in paths)

    if not skip_confirm:
        files = _discover_files(resolved_paths, glob_pattern)
        if not files:
            raise click.ClickException("No files found matching criteria")
        # Build chunk map for preview
        file_chunks: dict[str, list[tuple[str, str]]] = {}
        for f in files:
            text = f.read_text(errors="replace")
            display_path = str(f)
            chunks = _chunk_lines(text, display_path, chunk_size, overlap)
            if chunks:
                file_chunks[display_path] = chunks
        total_chunks = sum(len(cs) for cs in file_chunks.values())
        click.echo("Files to ingest:")
        max_name_len = max(len(n) for n in file_chunks)
        for name, chunks in file_chunks.items():
            padded = name.ljust(max_name_len)
            click.echo(f"  {padded}  {len(chunks)} chunks")
        click.echo(f"Total: {len(file_chunks)} files, {total_chunks} chunks")
        click.echo()
        if not click.confirm("Continue", default=False):
            raise click.ClickException("Aborted")

    total = ingest_files(
        collection,
        list(resolved_paths),
        glob=glob_pattern,
        model_id=model_id,
        chunk_size=chunk_size,
        overlap=overlap,
        loc_name=loc_name,
    )
    if total == 0:
        raise click.ClickException("No files found matching criteria")
    click.echo(f"Ingested {total} chunks into '{collection}'", err=True)


# ── analyze ──────────────────────────────────────────────


@fcio.command("analyze")
@click.argument(
    "analysis_type",
    required=False,
    default="review",
    type=click.Choice(["review", "overview"]),
)
@click.argument("files", nargs=-1)
@click.option("--model", default=None, help="Model to use for analysis")
@click.pass_context
def cmd_analyze(
    ctx: click.Context,
    analysis_type: str,
    files: tuple[str, ...],
    model: str | None,
) -> None:
    """Analyze code files with review or overview.

    \b
    Types:
      review    Code review for bugs, security, and quality (default)
      overview  Project architecture and component overview

    \b
    Examples:
      llm fcio analyze              # Auto-detect and review code in CWD
      llm fcio analyze review       # Review (same as default)
      llm fcio analyze overview     # Project overview
      llm fcio analyze review src/  # Review specific files/paths
      llm fcio analyze --model 120b # Use specific model
    """
    loc_name: str = ctx.obj["location"].name

    # Resolve files — CLI-only preview
    resolved_files = [Path(f).resolve() for f in files] if files else collect_code_files(Path.cwd())
    if not resolved_files:
        click.echo(f"No code files found in {Path.cwd()}")
        click.echo("Specify files explicitly or check file extensions")
        ctx.exit(1)
        return

    # Display file list with sizes and token estimate
    total_chars = 0
    for f in resolved_files:
        content = f.read_text()
        chars = len(content)
        total_chars += chars
        tokens = chars // 4
        size = f.stat().st_size
        click.echo(f"  {f.name}  ({size}b, ~{tokens} tokens)")
    total_tokens = total_chars // 4
    click.echo(f"Total: ~{total_tokens} tokens from {len(resolved_files)} files")
    click.echo()

    text = analyze_code(analysis_type, [str(f) for f in resolved_files], model, loc_name)
    click.echo(text)


@llm.hookimpl
def register_commands(cli: click.Group) -> None:
    install_renderer_patch()
    cli.add_command(fcio)


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


def _stream_chat_response(key: str, body: dict, api_base: str, render: bool) -> None:
    """Stream chat response with optional rich rendering."""
    try:
        renderer = _StreamingRenderer() if render else None
        with _make_client(verbose=_VERBOSE, debug=_DEBUG) as client:
            url = f"{api_base}/chat/completions"
            headers = _auth_headers(key)
            meta, content_iter = _iter_sse_content(client, url, headers, body)
            for content in content_iter:
                if render and renderer is not None:
                    renderer.feed(content)
                else:
                    click.echo(content, nl=False)
        if render and renderer is not None:
            renderer.flush()
        else:
            click.echo()
    except ApiError:
        raise
    except httpx.HTTPError as e:
        raise ApiError(f"Streaming error: {e}") from e


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
        _stream_chat_response(key, body, api_base, render)
    else:
        resp = api_request("POST", "/chat/completions", key, api_base, json_data=body)
        data = resp.json()
        content = _extract_content(data)
        if as_json:
            click.echo(json.dumps(data, indent=2))
        else:
            click.echo(content)
            if "usage" in data:
                u = data["usage"]
                click.echo(
                    f"\n⚡ {u.get('prompt_tokens', '?')}→{u.get('completion_tokens', '?')} tokens",
                    err=True,
                )
