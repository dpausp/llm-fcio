"""Adapter fakes for llm plugin types (llm.Collection, llm.Response, llm.Model).

These replace bare MagicMock() usage in tests where the llm plugin system
makes the real types unavailable as spec= targets at test time.
"""

from collections.abc import Callable, Iterator


class FakeResponse:
    """Minimal fake for llm.Response — iterable, yields configured chunks."""

    def __init__(self, chunks: tuple[str, ...] = ("Analysis result",)) -> None:
        self._chunks = chunks

    def __iter__(self) -> Iterator[str]:
        return iter(self._chunks)

    def text(self) -> str:
        return "".join(self._chunks)


class FakeModel:
    """Minimal fake for llm.Model — prompt() returns FakeResponse, has model_id."""

    model_id: str = "test-model"

    def __init__(self, response: FakeResponse | None = None, model_id: str = "test-model") -> None:
        self._response = response or FakeResponse()
        self.model_id = model_id
        self.last_prompt_args: tuple[object, ...] = ()
        self.last_prompt_kwargs: dict[str, object] = {}

    def prompt(self, *args: object, **kwargs: object) -> FakeResponse:
        self.last_prompt_args = args
        self.last_prompt_kwargs = kwargs
        return self._response


class FakeCollection:
    """Minimal fake for llm.Collection used by ingest tests."""

    def __init__(self, model_id: str = "bge-m3-567m") -> None:
        self._model_id = model_id
        self.embed_multi_calls: list[list[tuple[str, str]]] = []
        self._embed_multi_side_effect: Callable | None = None

    def model(self, model_id: str | None = None) -> FakeModel:
        return FakeModel(model_id=model_id or self._model_id)

    def embed_multi(self, entries: object, store: bool = False, batch_size: int = 100) -> None:
        consumed = list(entries)  # type: ignore[arg-type]
        self.embed_multi_calls.append(consumed)
        if self._embed_multi_side_effect:
            self._embed_multi_side_effect(entries)

    @classmethod
    def exists(cls, db: object, name: str) -> bool:
        return False
