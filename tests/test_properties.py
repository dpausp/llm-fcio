"""Property-based tests for pure functions in llm_fcio.

Uses Hypothesis for generative testing of _chunk_lines, _build_chat_body, and _b32c_encode.
"""

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from llm_fcio import _b32c_encode, _build_chat_body, _chunk_lines

# ── _chunk_lines properties ──────────────────────────────────────


@settings(max_examples=50)
@given(
    text=st.text(),
    filepath=st.text(min_size=1),
    chunk_size=st.integers(min_value=1, max_value=100),
)
def test_chunk_lines_empty_text_no_chunks(text: str, filepath: str, chunk_size: int) -> None:
    # Only check empty text property when text has no lines
    if not text.splitlines():
        result = _chunk_lines(text, filepath, chunk_size, 0)
        assert result == []


@settings(max_examples=50)
@given(
    text=st.text(min_size=1),
    filepath=st.text(min_size=1),
    chunk_size=st.integers(min_value=1, max_value=100),
)
def test_chunk_lines_all_chunks_prefixed_with_filepath(
    text: str, filepath: str, chunk_size: int
) -> None:
    result = _chunk_lines(text, filepath, chunk_size, 0)
    for chunk_id, _ in result:
        assert chunk_id.startswith(f"{filepath}:")


@settings(max_examples=50)
@given(
    text=st.text(min_size=1),
    filepath=st.text(min_size=1),
    chunk_size=st.integers(min_value=1, max_value=100),
    overlap=st.integers(min_value=0, max_value=99),
)
def test_chunk_lines_no_chunk_exceeds_chunk_size(
    text: str, filepath: str, chunk_size: int, overlap: int
) -> None:
    # Assume valid overlap
    assume(overlap < chunk_size)
    result = _chunk_lines(text, filepath, chunk_size, overlap)
    for _, chunk_text in result:
        assert len(chunk_text.splitlines()) <= chunk_size


@settings(max_examples=50)
@given(
    text=st.text(min_size=1),
    filepath=st.text(min_size=1),
    chunk_size=st.integers(min_value=1, max_value=100),
    overlap=st.integers(min_value=0, max_value=99),
)
def test_chunk_lines_all_input_lines_covered(
    text: str, filepath: str, chunk_size: int, overlap: int
) -> None:
    assume(overlap < chunk_size)
    assume("\r" not in text)
    # splitlines() splits on Unicode line separators (\v, \x1e, \u2028, etc.)
    # but "\n".join() only produces \n — so roundtrip fails for those chars
    assume(all(c == "\n" or c not in "\v\f\x1c\x1d\x1e\x85\u2028\u2029" for c in text))
    lines = text.splitlines()
    if not lines:
        return
    result = _chunk_lines(text, filepath, chunk_size, overlap)
    # Collect all lines from all chunks
    covered_lines: list[str] = []
    for _, chunk_text in result:
        covered_lines.extend(chunk_text.split("\n"))
    assert covered_lines == lines


@settings(max_examples=50)
@given(
    text=st.text(min_size=1),
    filepath=st.text(min_size=1),
    chunk_size=st.integers(min_value=1, max_value=100),
    overlap=st.integers(min_value=0, max_value=99),
)
def test_chunk_lines_chunks_in_order(
    text: str, filepath: str, chunk_size: int, overlap: int
) -> None:
    assume(overlap < chunk_size)
    result = _chunk_lines(text, filepath, chunk_size, overlap)
    # Extract start line numbers from chunk IDs (filepath:start-end)
    starts = [int(cid.split(":")[-1].split("-")[0]) for cid, _ in result]
    assert starts == sorted(starts)
    assert all(s >= 1 for s in starts)


@settings(max_examples=50)
@given(
    text=st.text(min_size=1),
    filepath=st.text(min_size=1),
    chunk_size=st.integers(min_value=1, max_value=100),
    overlap=st.integers(min_value=0, max_value=99),
)
def test_chunk_lines_chunk_texts_are_substrings_of_joined_input(
    text: str, filepath: str, chunk_size: int, overlap: int
) -> None:
    assume(overlap < chunk_size)
    result = _chunk_lines(text, filepath, chunk_size, overlap)
    # Each chunk_text is a contiguous slice of the original lines joined with \n
    lines = text.splitlines()
    for _, chunk_text in result:
        chunk_lines = chunk_text.splitlines()
        # Find the chunk lines as a contiguous subsequence of original lines
        chunk_len = len(chunk_lines)
        found = False
        for i in range(len(lines) - chunk_len + 1):
            if lines[i : i + chunk_len] == chunk_lines:
                found = True
                break
        assert found, f"Chunk text {chunk_lines!r} not found as contiguous slice of input"


# ── _build_chat_body properties ──────────────────────────────────

_message_strategy = st.dictionaries(
    st.sampled_from(["role", "content"]),
    st.text(),
    min_size=1,
    max_size=3,
)

_messages_strategy = st.lists(_message_strategy, max_size=10)


@settings(max_examples=50)
@given(
    model=st.text(),
    messages=_messages_strategy,
    temperature=st.floats(allow_nan=False, allow_infinity=False),
    max_tokens=st.one_of(st.none(), st.integers(min_value=1)),
)
def test_build_chat_body_has_required_keys(
    model: str, messages: list, temperature: float, max_tokens: int | None
) -> None:
    body = _build_chat_body(model, messages, temperature, max_tokens)
    assert "model" in body
    assert "messages" in body
    assert "temperature" in body


@settings(max_examples=50)
@given(
    model=st.text(),
    messages=_messages_strategy,
    temperature=st.floats(allow_nan=False, allow_infinity=False),
    max_tokens=st.one_of(st.none(), st.integers(min_value=1)),
)
def test_build_chat_body_values_match_inputs(
    model: str, messages: list, temperature: float, max_tokens: int | None
) -> None:
    body = _build_chat_body(model, messages, temperature, max_tokens)
    assert body["model"] == model
    assert body["messages"] is messages
    assert body["temperature"] == temperature


@settings(max_examples=50)
@given(
    model=st.text(),
    messages=_messages_strategy,
    temperature=st.floats(allow_nan=False, allow_infinity=False),
    max_tokens=st.one_of(st.none(), st.integers(min_value=1)),
)
def test_build_chat_body_max_tokens_present_iff_truthy(
    model: str, messages: list, temperature: float, max_tokens: int | None
) -> None:
    body = _build_chat_body(model, messages, temperature, max_tokens)
    if max_tokens:
        assert "max_tokens" in body
        assert body["max_tokens"] == max_tokens
    else:
        assert "max_tokens" not in body


@settings(max_examples=50)
@given(
    model=st.text(),
    messages=_messages_strategy,
    temperature=st.floats(allow_nan=False, allow_infinity=False),
    max_tokens=st.one_of(st.none(), st.integers(min_value=1)),
)
def test_build_chat_body_no_extra_keys(
    model: str, messages: list, temperature: float, max_tokens: int | None
) -> None:
    body = _build_chat_body(model, messages, temperature, max_tokens)
    allowed_keys = {"model", "messages", "temperature", "max_tokens"}
    assert set(body.keys()) <= allowed_keys


# ── _b32c_encode properties ──────────────────────────────────────

_B32C_CHARSET = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


@settings(max_examples=50)
@given(
    value=st.integers(min_value=0),
    length=st.integers(min_value=1, max_value=20),
)
def test_b32c_encode_output_length(value: int, length: int) -> None:
    assert len(_b32c_encode(value, length)) == length


@settings(max_examples=50)
@given(
    value=st.integers(min_value=0),
    length=st.integers(min_value=1, max_value=20),
)
def test_b32c_encode_all_chars_in_alphabet(value: int, length: int) -> None:
    result = _b32c_encode(value, length)
    assert set(result) <= _B32C_CHARSET


@settings(max_examples=50)
@given(
    value=st.integers(min_value=0),
    length=st.integers(min_value=1, max_value=20),
)
def test_b32c_encode_deterministic(value: int, length: int) -> None:
    assert _b32c_encode(value, length) == _b32c_encode(value, length)
