"""llm-rzob: Plugin für https://ai.rzob.fcio.net/openai/v1"""

import json
import click
import httpx
from httpx_sse import connect_sse
import llm
from pydantic import Field
from typing import Optional, Iterator

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
    json_data: Optional[dict] = None,
    params: Optional[dict] = None,
    stream: bool = False,
) -> httpx.Response:
    """Generic API request helper mit Auth + Error-Handling"""
    url = f"{API_BASE}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    client = httpx.Client(timeout=30.0)
    try:
        response = client.request(
            method, url, headers=headers, json=json_data, params=params, stream=stream
        )
        if response.status_code >= 400:
            try:
                err = response.json()
                msg = err.get("detail", err.get("error", {}).get("message", str(err)))
            except Exception:
                msg = response.text[:200]
            raise click.ClickException(f"{response.status_code}: {msg}")
        return response
    finally:
        client.close()


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


# ── Model Class ─────────────────────────────────────────


class RzobModel(llm.KeyModel):
    needs_key = KEY_NAME
    key_env_var = "FCIO_RZOB_API_KEY"
    can_stream = True

    class Options(llm.Options):
        temperature: Optional[float] = Field(
            description="Sampling temperature (0-2)", ge=0.0, le=2.0, default=None
        )
        max_tokens: Optional[int] = Field(
            description="Max tokens in response", ge=1, default=None
        )
        top_p: Optional[float] = Field(
            description="Nucleus sampling parameter", ge=0.0, le=1.0, default=None
        )

    def __init__(self, model_id: str, api_id: str):
        self.model_id = model_id  # "fcio-rzob/gpt-oss-20b"
        self.api_id = api_id      # "gpt-oss-20b" für API-Call

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
                    client, "POST", f"{API_BASE}/chat/completions",
                    headers=headers, json=body, timeout=None,
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
                    headers=headers, json=body, timeout=None,
                )
                resp.raise_for_status()
                data = resp.json()
                yield data["choices"][0]["message"]["content"]
                response.response_json = data


# ── Model Registration ──────────────────────────────────


@llm.hookimpl
def register_models(register):
    for m in _load_models():
        mid = m["id"]
        safe = m.get("safe_id", mid.replace(":", "-"))
        register(
            RzobModel(f"fcio-rzob/{safe}", mid),
            aliases=(safe,),
        )


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
            models.append({
                "id": mid,
                "safe_id": mid.replace(":", "-").replace(".", "_"),
            })
        _cache_path().write_text(json.dumps(models, indent=2))
        click.echo(f"Cached {len(models)} models", err=True)

    # ── models ─────────────────────────────────────────

    @rzob.command("models")
    @click.option("--json", "as_json", is_flag=True, help="Output as raw JSON")
    @click.option("--filter", "filt", help="Filter models by name substring")
    def cmd_models(as_json: bool, filt: Optional[str]):
        """List available models from the API"""
        key = get_api_key()
        resp = api_request("GET", "/models", key)
        models = resp.json().get("data", [])

        if filt:
            models = [m for m in models if filt.lower() in m.get("id", "").lower()]

        if as_json:
            click.echo(json.dumps(models, indent=2))
        else:
            click.echo(f"{'ID':<40} {'Context':>10} {'Vision':>8} {'Tools':>7}")
            click.echo("-" * 70)
            for m in models:
                mid = m.get("id", "unknown")[:38]
                ctx = m.get("context_window", m.get("max_tokens", "?"))
                vis = "✅" if m.get("vision") else "–"
                tools = "✅" if m.get("supports_tools", True) else "–"
                click.echo(f"{mid:<40} {str(ctx):>10} {vis:>8} {tools:>7}")

    # ── chat ───────────────────────────────────────────

    @rzob.command("chat")
    @click.argument("model_id")
    @click.argument("prompt", required=False)
    @click.option("-s", "--system", help="System prompt")
    @click.option("-t", "--temperature", type=float, default=0.7, help="Temperature")
    @click.option("-m", "--max-tokens", type=int, help="Max tokens")
    @click.option("--stream/--no-stream", default=True, help="Stream response")
    @click.option("--json", "as_json", is_flag=True, help="Output full JSON response")
    @click.option("-i", "--interactive", is_flag=True, help="Interactive chat mode")
    def cmd_chat(
        model_id: str,
        prompt: Optional[str],
        system: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
        stream: bool,
        as_json: bool,
        interactive: bool,
    ):
        """Test chat completions with a model"""
        key = get_api_key()

        messages = []
        if system:
            messages.append({"role": "system", "content": system})

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
            if not prompt:
                raise click.ClickException("Prompt required (or use --interactive)")
            messages.append({"role": "user", "content": prompt})
            body = _build_chat_body(model_id, messages, temperature, max_tokens)
            _send_chat_request(key, body, stream, as_json)

    # ── embed ──────────────────────────────────────────

    @rzob.command("embed")
    @click.argument("model_id")
    @click.argument("text", nargs=-1, required=True)
    @click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
    @click.option("-d", "--dimensions", type=int, help="Output dimension")
    def cmd_embed(model_id: str, text: tuple[str], as_json: bool, dimensions: Optional[int]):
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
                click.echo(f"Text {i+1}: [{len(vec)} dims] {vec[:5]}... (truncated)")
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


# ── Chat Helpers ────────────────────────────────────────


def _build_chat_body(
    model_id: str,
    messages: list,
    temperature: float,
    max_tokens: Optional[int],
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
