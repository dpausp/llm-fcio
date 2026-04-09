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
        # Messages bauen (minimal)
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
        
        # Body bauen MIT Options-Handling
        body = {"model": self.api_id, "messages": messages}
        if prompt.options.temperature is not None:
            body["temperature"] = prompt.options.temperature
        if prompt.options.max_tokens is not None:
            body["max_tokens"] = prompt.options.max_tokens
        if prompt.options.top_p is not None:
            body["top_p"] = prompt.options.top_p
        
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }
        
        if stream:
            body["stream"] = True
            with httpx.Client() as client:
                with connect_sse(
                    client, "POST", f"{API_BASE}/chat/completions",
                    headers=headers, json=body, timeout=None
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
                    headers=headers, json=body, timeout=None
                )
                resp.raise_for_status()
                data = resp.json()
                yield data["choices"][0]["message"]["content"]
                response.response_json = data

@llm.hookimpl
def register_models(register):
    for m in _load_models():
        mid = m["id"]
        safe = m.get("safe_id", mid.replace(":", "-"))
        register(
            RzobModel(f"fcio-rzob/{safe}", mid),
            aliases=(safe,)
        )

@llm.hookimpl  
def register_commands(cli):
    @cli.group()
    def rzob():
        "llm-rzob commands"
    
    @rzob.command()
    def refresh():
        key = llm.get_key("", KEY_NAME, "FCIO_RZOB_API_KEY")
        if not key:
            raise click.ClickException(f"Set key: llm keys set {KEY_NAME}")
        r = httpx.get(f"{API_BASE}/models", headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        raw = r.json()
        data = raw.get("data", raw if isinstance(raw, list) else [])
        models = []
        for m in data:
            mid = m["id"] if isinstance(m, dict) else str(m)
            models.append({
                "id": mid,
                "safe_id": mid.replace(":", "-").replace(".", "_")
            })
        _cache_path().write_text(json.dumps(models, indent=2))
        click.echo(f"Cached {len(models)} models", err=True)