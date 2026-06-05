"""Tests for utility helpers in llm_fcio.

Covers _b32c_encode, _generate_lid, _mask_auth_header, _extract_content.
All pure functions — no network, no API, no setup.
"""

import re
from typing import Any

import pytest

from llm_fcio import ApiError, _b32c_encode, _extract_content, _generate_lid, _mask_auth_header

# ── _b32c_encode ─────────────────────────────────────────────────


def test_b32c_encode_known_values() -> None:
    """Bekannte Integer → base32-crockford strings."""
    cases: list[tuple[int, int, str]] = [
        (0, 1, "0"),
        (0, 5, "00000"),
        (1, 1, "1"),
        (31, 1, "Z"),
        (32, 2, "10"),
        (42, 2, "1A"),
        # 64-bit all-ones encoded as 13 chars: F + 12×Z (nur 64 Bits = 13×5 - 1 padding)
        (0xFFFF_FFFF_FFFF_FFFF, 13, "FZZZZZZZZZZZZ"),
    ]
    for value, length, expected in cases:
        assert _b32c_encode(value, length) == expected, (
            f"_b32c_encode({value}, {length}) != {expected!r}"
        )


def test_b32c_encode_zero_length() -> None:
    """Länge 0 → leerer String."""
    assert _b32c_encode(12345, 0) == ""


def test_b32c_encode_reversible() -> None:
    """Gleicher Input → gleicher Output."""
    assert _b32c_encode(999, 5) == _b32c_encode(999, 5)


def test_b32c_encode_lsb_is_rightmost() -> None:
    """Niederwertigste 5 Bits sind das letzte Zeichen (nach Reverse)."""
    # value=32 (0b100000): LSB=0, next=1 → "10"
    assert _b32c_encode(32, 2) == "10"
    # value=33 (0b100001): LSB=1, next=1 → "11"
    assert _b32c_encode(33, 2) == "11"


# ── _generate_lid ────────────────────────────────────────────────


_LID_PATTERN = re.compile(r"^[0-9A-Z]{9}-[0-9A-Z]{4}$")


def test_generate_lid_format() -> None:
    """LID hat Format XXXXXXXXX-XXXX, nur base32-crockford."""
    lid = _generate_lid()
    assert _LID_PATTERN.match(lid), f"LID format mismatch: {lid!r}"


def test_generate_lid_length() -> None:
    """LID ist genau 14 Zeichen (9+1+4)."""
    assert len(_generate_lid()) == 14


def test_generate_lid_only_valid_chars() -> None:
    """LID enthält nur Zeichen aus _B32C plus Bindestrich."""
    lid = _generate_lid()
    valid = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ-")
    assert set(lid) <= valid, f"Invalid chars in LID: {lid!r}"


def test_generate_lid_has_hyphen() -> None:
    """LID enhält genau einen Bindestrich an Position 9."""
    lid = _generate_lid()
    assert lid[9] == "-"
    assert lid.count("-") == 1


def test_generate_lid_unique_across_calls() -> None:
    """Zwei aufeinanderfolgende LIDs sind unterschiedlich (ms-Basis)."""
    lids = {_generate_lid() for _ in range(10)}
    assert len(lids) == 10, "LIDs sollten unique sein"


# ── _mask_auth_header ────────────────────────────────────────────


def test_mask_auth_header_masks_authorization() -> None:
    """Authorization-Header wird maskiert."""
    assert _mask_auth_header("Authorization", "Bearer sk-real-key-123") == "Bearer sk-***..."


def test_mask_auth_header_masks_authorization_lowercase() -> None:
    """Case-insensitive: 'authorization' wird auch maskiert."""
    assert _mask_auth_header("authorization", "Bearer sk-real-key-456") == "Bearer sk-***..."


def test_mask_auth_header_passes_others() -> None:
    """Nicht-Authorization-Header werden unverändert durchgelassen."""
    cases: list[tuple[str, str]] = [
        ("Content-Type", "application/json"),
        ("X-Debug-ID", "abc-123"),
        ("Accept", "text/plain"),
        ("", "anything"),
    ]
    for name, value in cases:
        assert _mask_auth_header(name, value) == value, (
            f"Header {name!r} sollte unverändert bleiben"
        )


# ── _extract_content ─────────────────────────────────────────────


def test_extract_content_normal() -> None:
    """Normaler Fall: choices[0].message.content."""
    data = {"choices": [{"message": {"content": "Hello world"}}]}
    assert _extract_content(data) == "Hello world"


def test_extract_content_empty_string() -> None:
    """content ist leerer String."""
    data = {"choices": [{"message": {"content": ""}}]}
    assert _extract_content(data) == ""


def test_extract_content_missing_content_key() -> None:
    """message dict hat kein 'content'."""
    data = {"choices": [{"message": {"role": "assistant"}}]}
    assert _extract_content(data) == ""


def test_extract_content_missing_message() -> None:
    """choices[0] hat kein 'message'."""
    data = {"choices": [{"finish_reason": "stop"}]}
    assert _extract_content(data) == ""


def test_extract_content_empty_choices_list() -> None:
    """choices ist leere Liste → ApiError."""
    data = {"choices": []}
    with pytest.raises(ApiError, match="Empty response"):
        _extract_content(data)


def test_extract_content_missing_choices_key() -> None:
    """dict hat gar keinen 'choices'-Key → ApiError."""
    data: dict[str, Any] = {"id": "123"}
    with pytest.raises(ApiError, match="Empty response"):
        _extract_content(data)


def test_extract_content_choices_is_none() -> None:
    """choices ist None → ApiError."""
    data = {"choices": None}
    with pytest.raises(ApiError, match="Empty response"):
        _extract_content(data)


def test_extract_content_preserves_whitespace() -> None:
    """Whitespace im content bleibt erhalten."""
    data = {"choices": [{"message": {"content": "  hello\n  world  "}}]}
    assert _extract_content(data) == "  hello\n  world  "
