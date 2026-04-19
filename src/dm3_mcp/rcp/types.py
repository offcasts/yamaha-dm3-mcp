"""Value codecs for RCP wire representations <-> Python types."""
from __future__ import annotations

import math

NEG_INF_RAW = -32768
DEFAULT_DB_SCALE = 100


def db_to_raw(
    value_db: float,
    *,
    scale: int = DEFAULT_DB_SCALE,
    min_raw: int = -13800,
    max_raw: int = 1000,
) -> int:
    """Convert dB (float) to the RCP integer representation. `-inf` -> NEG_INF_RAW."""
    if math.isinf(value_db) and value_db < 0:
        return NEG_INF_RAW
    raw = round(value_db * scale)
    if raw < min_raw:
        raise ValueError(f"dB value {value_db} below minimum ({min_raw / scale})")
    if raw > max_raw:
        raise ValueError(f"dB value {value_db} above maximum ({max_raw / scale})")
    return raw


def raw_to_db(raw: int, *, scale: int = DEFAULT_DB_SCALE) -> float:
    if raw == NEG_INF_RAW:
        return float("-inf")
    return raw / scale


def pan_to_raw(pan: int) -> int:
    if not -63 <= pan <= 63:
        raise ValueError(f"Pan must be in -63..63, got {pan}")
    return pan


def raw_to_pan(raw: int) -> int:
    return int(raw)


def quote_if_needed(s: str) -> str:
    """RCP string values are quoted when they contain spaces or quotes."""
    if any(c in s for c in ' \t"') or s == "":
        escaped = s.replace('"', '\\"')
        return f'"{escaped}"'
    return s
