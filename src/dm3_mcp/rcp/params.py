"""Parse the vendored DM3 Parameters-2.txt into a typed registry.

Each line looks like:
  OK prminfo 0 "MIXER:Current/InCh/Fader/Level" 16 1 -32768 1000 -32768 "dB" integer any rw 100
Fields: Ok Action Index Address X Y Min Max Default Unit Type UI RW Scale [Pickoff]
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

TypeTag = Literal["integer", "string", "binary", "bool", "mtr", "none"]
RwFlag = Literal["r", "rw", "w", "--"]


@dataclass(frozen=True)
class ParamDef:
    address: str
    x_max: int
    y_max: int
    min: int | str
    max: int | str
    default: int | str
    unit: str
    type: TypeTag
    rw: RwFlag
    scale: int
    pickoff: str | None = None


_FIELD_RE = re.compile(r'(?:[^\s"]+|"[^"]*")+')


def _unquote(s: str) -> str:
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


def _coerce(value: str) -> int | str:
    unq = _unquote(value)
    try:
        return int(unq)
    except ValueError:
        return unq


def load_dm3_params(path: Path) -> dict[str, ParamDef]:
    """Return {address: ParamDef} for every 'OK prminfo' / 'OK mtrinfo' / 'OK scninfo' line."""
    params: dict[str, ParamDef] = {}
    for raw_line in path.read_text().splitlines():
        tokens = _FIELD_RE.findall(raw_line)
        if len(tokens) < 13:
            continue
        status = tokens[0].upper()
        action = tokens[1]
        if status not in ("OK", "OKM", "NOTIFY"):
            continue
        if action not in ("prminfo", "mtrinfo", "scninfo"):
            continue

        address = _unquote(tokens[3])
        x_max = int(tokens[4])
        y_max = int(tokens[5])
        min_v = _coerce(tokens[6])
        max_v = _coerce(tokens[7])
        default = _coerce(tokens[8])
        unit = _unquote(tokens[9])
        type_tag: TypeTag = tokens[10]  # type: ignore[assignment]
        rw: RwFlag = tokens[12]  # type: ignore[assignment]
        scale = int(tokens[13]) if len(tokens) > 13 and tokens[13].isdigit() else 1
        pickoff: str | None = None
        if action == "mtrinfo" and len(tokens) > 14:
            pickoff = _unquote(tokens[14])
            type_tag = "mtr"

        if type_tag == "integer" and min_v == 0 and max_v == 1:
            type_tag = "bool"

        params[address] = ParamDef(
            address=address,
            x_max=x_max,
            y_max=y_max,
            min=min_v,
            max=max_v,
            default=default,
            unit=unit,
            type=type_tag,
            rw=rw,
            scale=scale,
            pickoff=pickoff,
        )
    return params
