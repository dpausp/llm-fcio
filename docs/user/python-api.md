# Python API

Alle Plugin-Funktionen sind direkt importierbar — kein Click-Kontext, keine I/O-Seiteneffekte, pure data in/out.

```python
import llm_fcio as fcio

models = fcio.refresh_models()
caps = fcio.get_capabilities()
```

## Models / Cache

---

```{function} refresh_models(loc_name="rzob") -> list[dict]

Fetch available models from API and cache locally.

Returns list of ``{id, safe_id}`` dicts.
```

```{function} list_models(loc_name="rzob", filter=None) -> list[dict]

List models live from the API. ``filter`` is a client-side substring match on model ID.
```

```{function} get_model_info(model_id, loc_name="rzob") -> dict

Details for a single model. Raises ``ModelError`` on 404.
```

```{function} get_cached_models(loc_name="rzob") -> list[dict]

Read cached models from ``~/.llm/fcio_models_{location}.json`` — no API call.
```

## Endpoints

---

```{function} get_capabilities(loc_name="rzob") -> dict

Probe all endpoints and return a structured result:

```python
{
    "endpoint": {"name", "api_base", "auth"},
    "models": {"chat": [...], "embedding": [...], "other": [...], "counts": {...}},
    "features": {"chat_completions": {...}, "streaming": {...}, "embeddings": {...}},
}
```
```

## Tokens

---

```{function} estimate_tokens(text, model_id="gpt-oss:20b", loc_name="rzob") -> dict

Token count via API fallback: ``len(text) // 4`` heuristic.

Returns ``{"prompt_tokens": N}`` or ``{"prompt_tokens": N, "_fallback": True}``.
```

## Ingest

---

```{function} ingest_files(collection, paths, *, glob="*.md", model_id="bge-m3-567m", chunk_size=30, overlap=5, loc_name="rzob") -> int

Chunk and embed files into an ``llm`` embedding collection. ``paths`` can be a single path, a list of paths, or glob-based.

Returns total chunk count.
```

## Analyse

---

```{function} analyze_code(analysis_type="review", files=None, model_id=None, loc_name="rzob") -> str

Code review or overview via templates. ``analysis_type``: ``"review"`` or ``"overview"``.

``files`` is a list of paths. When ``None``, the current working directory is scanned automatically.

If ``model_id`` is ``None``, the default FCIO chat model is used.

Returns the generated analysis text.
```

## Chunken & File Discovery

---

```{function} discover_files(paths, glob_pattern) -> list[Path]

Discover files from paths, applying gitignore + hard-exclude filtering.
```

```{function} chunk_lines(text, filepath, chunk_size, overlap) -> list[tuple[str, str]]

Split text into line-based overlapping chunks. Returns ``(chunk_id, chunk_text)`` pairs.
```

```{function} collect_code_files(directory) -> list[Path]

Collect code files from directory using extension whitelist (``.py``, ``.js``, ``.ts``, …) and ``.gitignore`` filtering.
```
