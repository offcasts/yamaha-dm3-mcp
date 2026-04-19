"""Encode and decode RCP wire messages.

M0 findings folded in:
- OK responses include a trailing display-string token after the raw value;
  parsers ignore tokens past index 5 (only used for display, not state).
- `ssrecall_ex` takes a string bank ('scene_a' / 'scene_b'), not an int.
- There is no working scene-store command, so no `encode_store_scene`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


def _format_value(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(round(value)))
    if isinstance(value, str):
        # Always quote string values on the wire — the DM3 accepts quoted strings
        # universally and unquoted strings only when no special chars are present.
        # Quoting always is simpler and safer.
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    raise TypeError(f"Unsupported value type: {type(value).__name__}")


def encode_set(address: str, x: int, y: int, value) -> str:
    return f"set {address} {x} {y} {_format_value(value)}\n"


def encode_get(address: str, x: int, y: int) -> str:
    return f"get {address} {x} {y}\n"


def encode_ssrecall(bank: str, scene: int) -> str:
    """Recall scene by bank ('A' or 'B') and 1-based number.

    DM3 expects bank as the literal string 'scene_a' or 'scene_b'.
    """
    bank_upper = bank.upper()
    if bank_upper not in ("A", "B"):
        raise ValueError(f"bank must be 'A' or 'B', got {bank!r}")
    bank_token = "scene_a" if bank_upper == "A" else "scene_b"
    return f"ssrecall_ex {bank_token} {scene}\n"


def encode_sscurrent(bank: str) -> str:
    """Query the current scene number for the given bank ('A' or 'B')."""
    bank_upper = bank.upper()
    if bank_upper not in ("A", "B"):
        raise ValueError(f"bank must be 'A' or 'B', got {bank!r}")
    bank_token = "scene_a" if bank_upper == "A" else "scene_b"
    return f"sscurrent_ex {bank_token}\n"


@dataclass(frozen=True)
class ParsedResponse:
    kind: Literal["ok", "okm", "get", "notify", "error", "unknown"]
    raw: str
    action: str | None = None
    address: str | None = None
    x: int | None = None
    y: int | None = None
    value: int | str | None = None
    message: str | None = None


_TOKEN_RE = re.compile(r'(?:[^\s"]+|"[^"]*")+')


def _parse_value(tok: str) -> int | str:
    if tok.startswith('"') and tok.endswith('"'):
        return tok[1:-1]
    try:
        return int(tok)
    except ValueError:
        return tok


def parse_response(line: str) -> ParsedResponse:
    """Parse one line received from the DM3."""
    stripped = line.rstrip("\r\n")
    if not stripped:
        return ParsedResponse(kind="unknown", raw=stripped)

    tokens = _TOKEN_RE.findall(stripped)
    if not tokens:
        return ParsedResponse(kind="unknown", raw=stripped)

    head = tokens[0].upper()
    if head == "ERROR":
        return ParsedResponse(kind="error", raw=stripped, message=" ".join(tokens[1:]))

    if head == "NOTIFY":
        # NOTIFY set <Address> <X> <Y> <Value> [display]
        if len(tokens) >= 6 and tokens[1].lower() == "set":
            return ParsedResponse(
                kind="notify",
                raw=stripped,
                action="set",
                address=tokens[2],
                x=int(tokens[3]),
                y=int(tokens[4]),
                value=_parse_value(tokens[5]),
            )
        return ParsedResponse(kind="notify", raw=stripped)

    if head in ("OK", "OKM"):
        kind = "okm" if head == "OKM" else "ok"
        # OK <action> <Address> <X> <Y> [<Value> [display]]
        if len(tokens) >= 5:
            action = tokens[1].lower()
            if action == "get":
                value = _parse_value(tokens[5]) if len(tokens) > 5 else None
                return ParsedResponse(
                    kind="get",
                    raw=stripped,
                    action="get",
                    address=tokens[2],
                    x=int(tokens[3]),
                    y=int(tokens[4]),
                    value=value,
                )
            value = _parse_value(tokens[5]) if len(tokens) > 5 else None
            return ParsedResponse(
                kind=kind,
                raw=stripped,
                action=action,
                address=tokens[2],
                x=int(tokens[3]),
                y=int(tokens[4]),
                value=value,
            )
        return ParsedResponse(kind=kind, raw=stripped)

    return ParsedResponse(kind="unknown", raw=stripped)
