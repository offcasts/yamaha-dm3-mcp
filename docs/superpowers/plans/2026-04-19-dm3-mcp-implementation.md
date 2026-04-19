# Yamaha DM3 MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python/FastMCP server exposing ~33 Claude-facing tools for setting up a Yamaha DM3 digital mixer (labels, faders, sends, mute groups, scenes) over the Yamaha RCP protocol on TCP port 49280.

**Architecture:** Layered. Async TCP `RcpClient` (persistent connection, command queue, NOTIFY demux, keep-alive) ↔ in-memory `StateCache` mirrored from `NOTIFY` events ↔ FastMCP `server.py` registering primitive tools (1:1 RCP wrappers), macro tools (batched intents), and scene tools (recall/store + local JSON metadata).

**Tech Stack:** Python 3.11+, `uv` package manager, FastMCP (official MCP SDK), asyncio, pytest + pytest-asyncio, ruff.

**Reference documents** (required reading before starting):
- `docs/superpowers/specs/2026-04-19-dm3-mcp-design.md` — full design spec
- `data/DM3 Parameters-2.txt` — authoritative DM3 RCP parameter dump
- `DM3_osc_extracted.txt` — extracted OSC spec (for cross-reference)

---

## Prerequisites for the executing machine

1. **Python 3.11+** installed (`python --version`).
2. **`uv` package manager** installed (`curl -LsSf https://astral.sh/uv/install.sh | sh` or Windows equivalent).
3. **Network access to the DM3 console** — must be on the same LAN. Record the DM3's IP (default `192.168.0.128`). Verify with `ping <DM3_IP>` before starting.
4. **DM3 "For Mixer Control" enabled** in the console's NETWORK setup window (SETUP toolbar → NETWORK → "For Mixer Control" → Static IP).
5. **Git** available.

Tasks that require the real DM3 are marked **🎯 LIVE HARDWARE**. Skip them only if the console is unavailable; the plan notes every such branch.

---

## Phase 0 — Project bootstrap

### Task 1: Initialize `pyproject.toml`

**Files:**
- Create: `pyproject.toml`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "yamaha-dm3-mcp"
version = "0.1.0"
description = "MCP server for controlling a Yamaha DM3 digital mixer"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.2.0",
    "pydantic>=2.6",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.3",
    "ipython>=8.20",
]

[project.scripts]
dm3-mcp = "dm3_mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/dm3_mcp"]

[tool.ruff]
line-length = 110
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "live_hardware: requires a real DM3 on the network",
]
addopts = "-m 'not live_hardware'"
```

- [ ] **Step 2: Create package skeleton**

```bash
mkdir -p src/dm3_mcp/rcp src/dm3_mcp/state src/dm3_mcp/tools tests/unit tests/integration scripts
touch src/dm3_mcp/__init__.py src/dm3_mcp/rcp/__init__.py src/dm3_mcp/state/__init__.py src/dm3_mcp/tools/__init__.py
```

- [ ] **Step 3: Install deps in a venv**

```bash
uv venv
uv pip install -e ".[dev]"
```

Expected: `.venv/` created, all dependencies installed, no errors.

- [ ] **Step 4: Smoke test**

```bash
.venv/bin/python -c "import mcp, pytest; print('ok')"
```

Expected output: `ok`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/ tests/ scripts/
git commit -m "chore: bootstrap Python package with uv + FastMCP deps"
```

---

### Task 2: Add a pytest smoke test

**Files:**
- Create: `tests/unit/test_smoke.py`

- [ ] **Step 1: Write the smoke test**

```python
# tests/unit/test_smoke.py
def test_package_imports():
    import dm3_mcp
    assert dm3_mcp is not None
```

- [ ] **Step 2: Run it**

```bash
.venv/bin/pytest tests/unit/test_smoke.py -v
```

Expected: `1 passed`

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_smoke.py
git commit -m "test: add package import smoke test"
```

---

## Phase 1 (M0) — Empirical probe against the real DM3

Goal: confirm or refute protocol assumptions before we build on them. Every later milestone depends on what this phase discovers. Every test result is appended to `data/probe-results-<timestamp>.json`.

### Task 3: Minimal standalone TCP probe

**Files:**
- Create: `scripts/probe.py`

This script intentionally does NOT depend on the `dm3_mcp` package yet — we want it to stay runnable as a pure-stdlib diagnostic.

- [ ] **Step 1: Write `scripts/probe.py`**

```python
#!/usr/bin/env python3
"""Empirical probe of a real Yamaha DM3 over RCP.

Usage: python scripts/probe.py --host 192.168.0.128 [--port 49280]

Writes results to data/probe-results-<timestamp>.json.
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


async def send_and_read(reader, writer, line: str, timeout: float = 2.0) -> str:
    writer.write((line + "\n").encode())
    await writer.drain()
    try:
        resp = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=timeout)
        return resp.decode().rstrip()
    except asyncio.TimeoutError:
        return "<TIMEOUT>"


async def drain_notifies(reader, duration_s: float) -> list[str]:
    """Read any NOTIFY lines for `duration_s` seconds and return them."""
    lines: list[str] = []
    end = time.monotonic() + duration_s
    while time.monotonic() < end:
        try:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            line = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=remaining)
            lines.append(line.decode().rstrip())
        except asyncio.TimeoutError:
            break
    return lines


async def run_probe(host: str, port: int) -> dict:
    results: dict = {
        "host": host,
        "port": port,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tests": [],
    }

    def record(name: str, sent: str, response: str, notes: str = "") -> None:
        results["tests"].append(
            {"name": name, "sent": sent, "response": response, "notes": notes}
        )
        print(f"[{name}] {sent!r} -> {response!r}  {notes}")

    reader, writer = await asyncio.open_connection(host, port)
    try:
        # 1. Baseline: read any system greeting (DM3 may be silent)
        try:
            greeting = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=0.5)
            record("baseline_greeting", "", greeting.decode().rstrip())
        except asyncio.TimeoutError:
            record("baseline_greeting", "", "<silent>", "DM3 did not send a greeting")

        # 2. Documented: set fader on ch 1 to -10 dB
        record(
            "set_inch_fader",
            "set MIXER:Current/InCh/Fader/Level 0 0 -1000",
            await send_and_read(
                reader, writer, "set MIXER:Current/InCh/Fader/Level 0 0 -1000"
            ),
        )

        # 3. Documented: read it back
        record(
            "get_inch_fader",
            "get MIXER:Current/InCh/Fader/Level 0 0",
            await send_and_read(reader, writer, "get MIXER:Current/InCh/Fader/Level 0 0"),
        )

        # 4. Documented: label ch 1
        record(
            "set_inch_label",
            'set MIXER:Current/InCh/Label/Name 0 0 "PROBE"',
            await send_and_read(
                reader, writer, 'set MIXER:Current/InCh/Label/Name 0 0 "PROBE"'
            ),
        )

        # 5. Documented: HA gain
        record(
            "set_ha_gain",
            "set IO:Current/InCh/HAGain 0 0 20",
            await send_and_read(reader, writer, "set IO:Current/InCh/HAGain 0 0 20"),
        )

        # 6. Value semantics: -inf via -32768
        record(
            "set_fader_neg_inf",
            "set MIXER:Current/InCh/Fader/Level 0 0 -32768",
            await send_and_read(
                reader, writer, "set MIXER:Current/InCh/Fader/Level 0 0 -32768"
            ),
        )

        # 7. Value semantics: out-of-range (should clamp with OKm)
        record(
            "set_fader_above_max",
            "set MIXER:Current/InCh/Fader/Level 0 0 9999",
            await send_and_read(
                reader, writer, "set MIXER:Current/InCh/Fader/Level 0 0 9999"
            ),
            "Expect OKm with value clamped to 1000",
        )

        # 8. Value semantics: string with spaces
        record(
            "set_label_spaces",
            'set MIXER:Current/InCh/Label/Name 0 0 "Lead Vox"',
            await send_and_read(
                reader, writer, 'set MIXER:Current/InCh/Label/Name 0 0 "Lead Vox"'
            ),
        )

        # 9. Send: enable InCh 1 -> Mix 1
        record(
            "set_send_on",
            "set MIXER:Current/InCh/ToMix/On 0 0 1",
            await send_and_read(
                reader, writer, "set MIXER:Current/InCh/ToMix/On 0 0 1"
            ),
        )

        # 10. Mute group control
        record(
            "set_mute_group",
            "set MIXER:Current/MuteGrpCtrl/On 0 0 1",
            await send_and_read(
                reader, writer, "set MIXER:Current/MuteGrpCtrl/On 0 0 1"
            ),
        )

        # 11. 🎯 Undocumented patch probes (Rivage-style)
        for src in ["DANTE1", "AN1", "USB1"]:
            record(
                f"set_inch_patch_{src}",
                f'set MIXER:Current/InCh/Patch 0 0 "{src}"',
                await send_and_read(
                    reader, writer, f'set MIXER:Current/InCh/Patch 0 0 "{src}"'
                ),
                "UNDOCUMENTED — Rivage-style patch",
            )
        record(
            "get_inch_patch",
            "get MIXER:Current/InCh/Patch 0 0",
            await send_and_read(reader, writer, "get MIXER:Current/InCh/Patch 0 0"),
            "UNDOCUMENTED — can we at least read it?",
        )

        # 12. 🎯 Undocumented DM7-style PatchSelect
        record(
            "set_inch_patchselect",
            "set MIXER:Current/InCh/PatchSelect 0 0 1",
            await send_and_read(
                reader, writer, "set MIXER:Current/InCh/PatchSelect 0 0 1"
            ),
            "UNDOCUMENTED — DM7-style",
        )

        # 13. 🎯 Undocumented InCh/Port (DM7 namespace)
        record(
            "get_inch_port",
            "get MIXER:Current/InCh/Port 0 0",
            await send_and_read(reader, writer, "get MIXER:Current/InCh/Port 0 0"),
            "UNDOCUMENTED — DM7-style port address",
        )

        # 14. Scene: current scene query
        record(
            "sscurrent_a",
            "sscurrent_ex scene_a",
            await send_and_read(reader, writer, "sscurrent_ex scene_a"),
        )

        # 15. Scene: store to B99 (should be safe scratch slot — confirm empty first!)
        print("\n⚠️  ABOUT TO STORE SCENE B99. Ensure you don't care about that slot.")
        input("Press ENTER to continue or Ctrl-C to abort: ")
        record(
            "store_scene_b99",
            "set MIXER:Lib/Bank/Scene/Store 1 99",
            await send_and_read(reader, writer, "set MIXER:Lib/Bank/Scene/Store 1 99"),
        )

        # 16. Scene: recall B99
        record(
            "recall_scene_b99",
            "ssrecall_ex 1 99",
            await send_and_read(reader, writer, "ssrecall_ex 1 99", timeout=5.0),
        )
        # Drain any NOTIFY burst from the recall
        burst = await drain_notifies(reader, 2.0)
        results["tests"].append(
            {
                "name": "recall_notify_burst",
                "sent": "<drain>",
                "response": f"{len(burst)} lines",
                "lines": burst,
            }
        )

        # 17. NOTIFY capture: user should move a fader on the console
        print("\n👉 Move input fader 1 on the DM3 surface in the next 10 seconds.")
        burst = await drain_notifies(reader, 10.0)
        results["tests"].append(
            {
                "name": "surface_notify_capture",
                "sent": "<drain>",
                "response": f"{len(burst)} lines",
                "lines": burst,
            }
        )

        # 18. Rate limit discovery: burst 50 identical gets
        t0 = time.monotonic()
        for _ in range(50):
            writer.write(b"get MIXER:Current/InCh/Fader/Level 0 0\n")
        await writer.drain()
        count = 0
        errors = 0
        while time.monotonic() - t0 < 3.0:
            try:
                line = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=0.5)
                decoded = line.decode().rstrip()
                if decoded.startswith("ERROR"):
                    errors += 1
                count += 1
                if count >= 50:
                    break
            except asyncio.TimeoutError:
                break
        record(
            "rate_limit_burst_50",
            "<50 back-to-back gets>",
            f"got {count} responses, {errors} errors in {time.monotonic() - t0:.2f}s",
        )

    finally:
        writer.close()
        await writer.wait_closed()

    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    return results


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True, help="DM3 IP address")
    p.add_argument("--port", type=int, default=49280)
    p.add_argument("--out-dir", default="data", help="Directory for results")
    args = p.parse_args()

    results = asyncio.run(run_probe(args.host, args.port))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"probe-results-{ts}.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\n✅ Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Commit before running (so results are a separate commit)**

```bash
git add scripts/probe.py
git commit -m "feat(probe): standalone DM3 empirical discovery script"
```

---

### Task 4: 🎯 LIVE HARDWARE — Run the probe

- [ ] **Step 1: Verify DM3 connectivity**

```bash
ping -c 3 192.168.0.128   # substitute actual DM3 IP
```

Expected: 3 replies, < 10 ms latency.

- [ ] **Step 2: Run the probe**

```bash
.venv/bin/python scripts/probe.py --host 192.168.0.128
```

Expected: script walks through ~18 tests, prompts you twice (scene store confirmation, fader move), completes, writes `data/probe-results-<timestamp>.json`.

- [ ] **Step 3: Inspect results**

Read the JSON file. For each test, note:
- Did it return `OK`, `OKm`, `ERROR`, or `<TIMEOUT>`?
- Were undocumented patch addresses accepted (any non-ERROR)?
- Did surface-NOTIFY capture produce lines when you moved a fader?

- [ ] **Step 4: Commit findings**

```bash
git add data/probe-results-*.json
git commit -m "chore(probe): record M0 empirical results"
```

- [ ] **Step 5: Write findings summary**

Create `docs/superpowers/specs/M0-probe-findings.md` summarizing:
- Confirmed behaviors (what worked as spec'd)
- Surprises (clamping, NOTIFY frequency, quoting rules)
- Patching verdict (direct patch supported? read-only? not at all?)
- Any deviation from the design spec that requires a spec update

Commit:

```bash
git add docs/superpowers/specs/M0-probe-findings.md
git commit -m "docs: M0 probe findings summary"
```

---

## Phase 2 (M1) — RCP client, codec, parameter schema

### Task 5: Parameter file parser — failing test first

**Files:**
- Create: `tests/unit/test_params.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_params.py
from pathlib import Path
import pytest

from dm3_mcp.rcp.params import ParamDef, load_dm3_params


@pytest.fixture
def params():
    path = Path(__file__).parents[2] / "data" / "DM3 Parameters-2.txt"
    return load_dm3_params(path)


def test_load_returns_dict(params):
    assert isinstance(params, dict)
    assert len(params) > 100  # DM3 dump has ~173 entries


def test_fader_level_is_parsed(params):
    fader = params["MIXER:Current/InCh/Fader/Level"]
    assert isinstance(fader, ParamDef)
    assert fader.x_max == 16
    assert fader.y_max == 1
    assert fader.min == -32768
    assert fader.max == 1000
    assert fader.unit == "dB"
    assert fader.type == "integer"
    assert fader.rw == "rw"
    assert fader.scale == 100


def test_read_only_param_detected(params):
    role = params["MIXER:Current/StInCh/Role"]
    assert role.rw == "r"


def test_bool_inferred_from_integer_0_1(params):
    on = params["MIXER:Current/InCh/Fader/On"]
    assert on.type == "bool"  # promoted from integer when min=0 max=1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/test_params.py -v
```

Expected: ImportError or ModuleNotFoundError for `dm3_mcp.rcp.params`.

---

### Task 6: Parameter parser implementation

**Files:**
- Create: `src/dm3_mcp/rcp/params.py`

- [ ] **Step 1: Write the parser**

```python
# src/dm3_mcp/rcp/params.py
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
    pickoff: str | None = None  # only populated for meter entries


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

        # Promote integer 0..1 to bool for nicer downstream types
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
```

- [ ] **Step 2: Run tests**

```bash
.venv/bin/pytest tests/unit/test_params.py -v
```

Expected: `4 passed`.

- [ ] **Step 3: Commit**

```bash
git add src/dm3_mcp/rcp/params.py tests/unit/test_params.py
git commit -m "feat(rcp): parse DM3 Parameters-2.txt into typed ParamDef registry"
```

---

### Task 7: dB codec — failing test

**Files:**
- Create: `tests/unit/test_types.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_types.py
import math

import pytest

from dm3_mcp.rcp.types import db_to_raw, raw_to_db


def test_zero_db():
    assert db_to_raw(0.0) == 0
    assert raw_to_db(0) == 0.0


def test_positive_db():
    assert db_to_raw(10.0) == 1000
    assert raw_to_db(1000) == 10.0


def test_negative_db():
    assert db_to_raw(-13.8) == -1380
    assert raw_to_db(-1380) == -13.8


def test_neg_infinity():
    assert db_to_raw(float("-inf")) == -32768
    assert math.isinf(raw_to_db(-32768)) and raw_to_db(-32768) < 0


def test_clamp_above_max():
    with pytest.raises(ValueError):
        db_to_raw(20.0, min_raw=-32768, max_raw=1000)


def test_clamp_below_min():
    with pytest.raises(ValueError):
        db_to_raw(-200.0, min_raw=-32768, max_raw=1000)
```

- [ ] **Step 2: Run it**

```bash
.venv/bin/pytest tests/unit/test_types.py -v
```

Expected: ImportError.

---

### Task 8: dB codec implementation

**Files:**
- Create: `src/dm3_mcp/rcp/types.py`

- [ ] **Step 1: Write**

```python
# src/dm3_mcp/rcp/types.py
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
    """Pan already integer in -63..63 range. Validates only."""
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
```

- [ ] **Step 2: Run tests**

```bash
.venv/bin/pytest tests/unit/test_types.py -v
```

Expected: `6 passed`.

- [ ] **Step 3: Commit**

```bash
git add src/dm3_mcp/rcp/types.py tests/unit/test_types.py
git commit -m "feat(rcp): dB/pan codecs and string quoting"
```

---

### Task 9: Message encoder — failing test

**Files:**
- Create: `tests/unit/test_codec.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_codec.py
from dm3_mcp.rcp.codec import encode_set, encode_get, encode_ssrecall


def test_encode_set_integer():
    line = encode_set("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)
    assert line == "set MIXER:Current/InCh/Fader/Level 0 0 -1000\n"


def test_encode_set_string_simple():
    line = encode_set("MIXER:Current/InCh/Label/Name", 0, 0, "Kick")
    assert line == 'set MIXER:Current/InCh/Label/Name 0 0 "Kick"\n'


def test_encode_set_string_with_spaces():
    line = encode_set("MIXER:Current/InCh/Label/Name", 0, 0, "Lead Vox")
    assert line == 'set MIXER:Current/InCh/Label/Name 0 0 "Lead Vox"\n'


def test_encode_get():
    assert encode_get("MIXER:Current/InCh/Fader/Level", 0, 0) == "get MIXER:Current/InCh/Fader/Level 0 0\n"


def test_encode_ssrecall():
    assert encode_ssrecall(0, 5) == "ssrecall_ex 0 5\n"
```

- [ ] **Step 2: Run it**

```bash
.venv/bin/pytest tests/unit/test_codec.py -v
```

Expected: ImportError.

---

### Task 10: Message encoder implementation

**Files:**
- Create: `src/dm3_mcp/rcp/codec.py`

- [ ] **Step 1: Write**

```python
# src/dm3_mcp/rcp/codec.py
"""Encode and decode RCP wire messages."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .types import quote_if_needed


def _format_value(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(round(value)))
    if isinstance(value, str):
        return quote_if_needed(value)
    raise TypeError(f"Unsupported value type: {type(value).__name__}")


def encode_set(address: str, x: int, y: int, value) -> str:
    return f"set {address} {x} {y} {_format_value(value)}\n"


def encode_get(address: str, x: int, y: int) -> str:
    return f"get {address} {x} {y}\n"


def encode_ssrecall(bank: int, scene: int) -> str:
    return f"ssrecall_ex {bank} {scene}\n"


def encode_store_scene(bank: int, scene: int) -> str:
    return f"set MIXER:Lib/Bank/Scene/Store {bank} {scene}\n"


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


def _parse_value(tok: str) -> int | str:
    if tok.startswith('"') and tok.endswith('"'):
        return tok[1:-1]
    try:
        return int(tok)
    except ValueError:
        return tok


def parse_response(line: str) -> ParsedResponse:
    """Parse one line received from the DM3."""
    import re

    stripped = line.rstrip("\r\n")
    if not stripped:
        return ParsedResponse(kind="unknown", raw=stripped)

    # Tokenize respecting quoted strings
    tokens = re.findall(r'(?:[^\s"]+|"[^"]*")+', stripped)
    if not tokens:
        return ParsedResponse(kind="unknown", raw=stripped)

    head = tokens[0].upper()
    if head == "ERROR":
        return ParsedResponse(kind="error", raw=stripped, message=" ".join(tokens[1:]))

    if head == "NOTIFY":
        # NOTIFY set <Address> <X> <Y> <Value>
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
        # OK <action> <Address> <X> <Y> [<Value>]
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
```

- [ ] **Step 2: Add parse tests**

Append to `tests/unit/test_codec.py`:

```python
from dm3_mcp.rcp.codec import parse_response


def test_parse_ok_set():
    r = parse_response("OK set MIXER:Current/InCh/Fader/Level 0 0 -1000")
    assert r.kind == "ok"
    assert r.address == "MIXER:Current/InCh/Fader/Level"
    assert r.x == 0 and r.y == 0
    assert r.value == -1000


def test_parse_okm_clamped():
    r = parse_response("OKm set MIXER:Current/InCh/Fader/Level 0 0 1000")
    assert r.kind == "okm"
    assert r.value == 1000


def test_parse_notify():
    r = parse_response("NOTIFY set MIXER:Current/InCh/Fader/Level 5 0 -500")
    assert r.kind == "notify"
    assert r.x == 5
    assert r.value == -500


def test_parse_get_with_string_value():
    r = parse_response('OK get MIXER:Current/InCh/Label/Name 0 0 "Lead Vox"')
    assert r.kind == "get"
    assert r.value == "Lead Vox"


def test_parse_error():
    r = parse_response("ERROR parameter out of range")
    assert r.kind == "error"
    assert r.message == "parameter out of range"
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest tests/unit/test_codec.py -v
```

Expected: `10 passed`.

- [ ] **Step 4: Commit**

```bash
git add src/dm3_mcp/rcp/codec.py tests/unit/test_codec.py
git commit -m "feat(rcp): message encode/decode with quoted-string and NOTIFY support"
```

---

### Task 11: RcpClient skeleton — failing test with mock server

**Files:**
- Create: `tests/unit/test_client.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_client.py
import asyncio
import pytest

from dm3_mcp.rcp.client import RcpClient


class FakeDM3:
    """Serves one connection, echoes a canned response per command."""

    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.received: list[str] = []
        self._server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, host="127.0.0.1", port=0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        while True:
            try:
                line = await reader.readuntil(b"\n")
            except asyncio.IncompleteReadError:
                break
            cmd = line.decode().rstrip()
            self.received.append(cmd)
            response = self.responses.get(cmd, "ERROR unknown")
            writer.write((response + "\n").encode())
            await writer.drain()


@pytest.mark.asyncio
async def test_set_returns_ok():
    fake = FakeDM3(
        {
            "set MIXER:Current/InCh/Fader/Level 0 0 -1000": (
                "OK set MIXER:Current/InCh/Fader/Level 0 0 -1000"
            )
        }
    )
    await fake.start()
    try:
        client = RcpClient("127.0.0.1", fake.port)
        await client.connect()
        result = await client.set("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)
        assert result.kind == "ok"
        assert result.value == -1000
        await client.close()
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_get_returns_value():
    fake = FakeDM3(
        {
            "get MIXER:Current/InCh/Fader/Level 0 0": (
                "OK get MIXER:Current/InCh/Fader/Level 0 0 -1000"
            )
        }
    )
    await fake.start()
    try:
        client = RcpClient("127.0.0.1", fake.port)
        await client.connect()
        result = await client.get("MIXER:Current/InCh/Fader/Level", 0, 0)
        assert result.value == -1000
        await client.close()
    finally:
        await fake.stop()
```

- [ ] **Step 2: Run it — expect ImportError**

```bash
.venv/bin/pytest tests/unit/test_client.py -v
```

---

### Task 12: RcpClient implementation

**Files:**
- Create: `src/dm3_mcp/rcp/client.py`

- [ ] **Step 1: Write**

```python
# src/dm3_mcp/rcp/client.py
"""Async TCP client for Yamaha RCP."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .codec import (
    ParsedResponse,
    encode_get,
    encode_set,
    encode_ssrecall,
    encode_store_scene,
    parse_response,
)

log = logging.getLogger(__name__)

NotifyHandler = Callable[[ParsedResponse], Awaitable[None]]

MSG_GAP_S = 0.005         # 5 ms between sends (matches Companion MSG_DELAY)
KEEPALIVE_S = 10.0
RESPONSE_TIMEOUT_S = 2.0


@dataclass
class SetResult:
    kind: str            # "ok" or "okm"
    value: int | str | None
    clamped: bool        # True when console responded OKm


@dataclass
class GetResult:
    value: int | str | None


class RcpError(Exception):
    def __init__(self, reason: str, command: str):
        super().__init__(f"{reason} (command: {command!r})")
        self.reason = reason
        self.command = command


class RcpTimeout(Exception):
    pass


class ConnectionLost(Exception):
    pass


class RcpClient:
    def __init__(self, host: str, port: int = 49280):
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: asyncio.Queue[tuple[str, asyncio.Future[ParsedResponse]]] = asyncio.Queue()
        self._notify_handlers: list[NotifyHandler] = []
        self._reader_task: asyncio.Task | None = None
        self._writer_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._current: asyncio.Future[ParsedResponse] | None = None
        self._closing = False

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        self._closing = False
        self._reader_task = asyncio.create_task(self._read_loop())
        self._writer_task = asyncio.create_task(self._write_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def close(self) -> None:
        self._closing = True
        if self._keepalive_task:
            self._keepalive_task.cancel()
        if self._writer_task:
            self._writer_task.cancel()
        if self._reader_task:
            self._reader_task.cancel()
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        # Fail any pending command
        while not self._pending.empty():
            _, fut = await self._pending.get()
            if not fut.done():
                fut.set_exception(ConnectionLost("client closed"))

    def on_notify(self, handler: NotifyHandler) -> None:
        self._notify_handlers.append(handler)

    async def set(self, address: str, x: int, y: int, value) -> SetResult:
        line = encode_set(address, x, y, value)
        resp = await self._send(line)
        if resp.kind == "error":
            raise RcpError(resp.message or "unknown", line.strip())
        return SetResult(kind=resp.kind, value=resp.value, clamped=(resp.kind == "okm"))

    async def get(self, address: str, x: int, y: int) -> GetResult:
        line = encode_get(address, x, y)
        resp = await self._send(line)
        if resp.kind == "error":
            raise RcpError(resp.message or "unknown", line.strip())
        return GetResult(value=resp.value)

    async def recall_scene(self, bank: int, scene: int) -> None:
        line = encode_ssrecall(bank, scene)
        resp = await self._send(line, timeout=5.0)
        if resp.kind == "error":
            raise RcpError(resp.message or "unknown", line.strip())

    async def store_scene(self, bank: int, scene: int) -> None:
        line = encode_store_scene(bank, scene)
        resp = await self._send(line)
        if resp.kind == "error":
            raise RcpError(resp.message or "unknown", line.strip())

    async def _send(self, line: str, timeout: float = RESPONSE_TIMEOUT_S) -> ParsedResponse:
        if self._writer is None or self._closing:
            raise ConnectionLost("not connected")
        fut: asyncio.Future[ParsedResponse] = asyncio.get_event_loop().create_future()
        await self._pending.put((line, fut))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as e:
            raise RcpTimeout(f"no response to {line.strip()!r}") from e

    async def _write_loop(self) -> None:
        try:
            while not self._closing:
                line, fut = await self._pending.get()
                self._current = fut
                assert self._writer is not None
                self._writer.write(line.encode())
                await self._writer.drain()
                await asyncio.sleep(MSG_GAP_S)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.exception("writer loop died: %s", e)

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while not self._closing:
                raw = await self._reader.readuntil(b"\n")
                resp = parse_response(raw.decode())
                if resp.kind == "notify":
                    for h in self._notify_handlers:
                        asyncio.create_task(h(resp))
                    continue
                if self._current is not None and not self._current.done():
                    self._current.set_result(resp)
                    self._current = None
        except asyncio.IncompleteReadError:
            pass
        except asyncio.CancelledError:
            pass

    async def _keepalive_loop(self) -> None:
        try:
            while not self._closing:
                await asyncio.sleep(KEEPALIVE_S)
                try:
                    await self._send(encode_get("IO:Current/Dev/SystemStatus", 0, 0), timeout=3.0)
                except (RcpTimeout, ConnectionLost):
                    log.warning("keepalive failed; connection may be dead")
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 2: Run tests**

```bash
.venv/bin/pytest tests/unit/test_client.py -v
```

Expected: `2 passed`.

- [ ] **Step 3: Commit**

```bash
git add src/dm3_mcp/rcp/client.py tests/unit/test_client.py
git commit -m "feat(rcp): async RcpClient with command queue + NOTIFY fanout"
```

---

### Task 13: NOTIFY handler test

**Files:**
- Modify: `tests/unit/test_client.py`

- [ ] **Step 1: Add test**

Append:

```python
@pytest.mark.asyncio
async def test_notify_handler_receives_event():
    received: list[ParsedResponse] = []

    fake = FakeDM3({})
    await fake.start()

    # Monkey-patch FakeDM3 to send a NOTIFY after a short delay
    async def _handle_with_notify(reader, writer):
        await asyncio.sleep(0.05)
        writer.write(b"NOTIFY set MIXER:Current/InCh/Fader/Level 3 0 -500\n")
        await writer.drain()
        await asyncio.sleep(0.2)

    fake._handle = _handle_with_notify  # type: ignore[assignment]

    try:
        client = RcpClient("127.0.0.1", fake.port)

        async def handler(ev):
            received.append(ev)

        client.on_notify(handler)
        await client.connect()
        await asyncio.sleep(0.2)
        await client.close()
        assert len(received) == 1
        assert received[0].x == 3
        assert received[0].value == -500
    finally:
        await fake.stop()
```

Import at top:

```python
from dm3_mcp.rcp.codec import ParsedResponse
```

- [ ] **Step 2: Run**

```bash
.venv/bin/pytest tests/unit/test_client.py::test_notify_handler_receives_event -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_client.py
git commit -m "test(rcp): verify NOTIFY handler dispatch"
```

---

### Task 14: 🎯 LIVE HARDWARE — client integration test

**Files:**
- Create: `tests/integration/test_live_client.py`

- [ ] **Step 1: Write**

```python
# tests/integration/test_live_client.py
"""Live-DM3 integration tests. Enable with DM3_HOST env var.

Run with:  DM3_HOST=192.168.0.128 pytest -m live_hardware -v
"""
import os
import pytest

from dm3_mcp.rcp.client import RcpClient

pytestmark = pytest.mark.live_hardware

HOST = os.environ.get("DM3_HOST")


@pytest.mark.skipif(not HOST, reason="DM3_HOST not set")
@pytest.mark.asyncio
async def test_live_set_and_get_fader():
    client = RcpClient(HOST)
    await client.connect()
    try:
        await client.set("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)
        result = await client.get("MIXER:Current/InCh/Fader/Level", 0, 0)
        assert result.value == -1000
    finally:
        # restore
        await client.set("MIXER:Current/InCh/Fader/Level", 0, 0, -32768)
        await client.close()


@pytest.mark.skipif(not HOST, reason="DM3_HOST not set")
@pytest.mark.asyncio
async def test_live_label_roundtrip():
    client = RcpClient(HOST)
    await client.connect()
    try:
        await client.set("MIXER:Current/InCh/Label/Name", 0, 0, "LIVE")
        result = await client.get("MIXER:Current/InCh/Label/Name", 0, 0)
        assert result.value == "LIVE"
    finally:
        await client.close()
```

- [ ] **Step 2: Run with real DM3**

```bash
DM3_HOST=192.168.0.128 .venv/bin/pytest tests/integration/test_live_client.py -m live_hardware -v
```

Expected: both tests pass; ch 1 fader visibly moves and label updates on console.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_live_client.py
git commit -m "test(integration): live DM3 client round-trip"
```

---

## Phase 3 (M2) — State cache

### Task 15: CachedValue + StateCache skeleton

**Files:**
- Create: `tests/unit/test_cache.py`
- Create: `src/dm3_mcp/state/cache.py`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_cache.py
import time

from dm3_mcp.state.cache import StateCache


def test_record_set_stores_value_with_source():
    cache = StateCache()
    cache.record_set("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)
    entry = cache.get("MIXER:Current/InCh/Fader/Level", 0, 0)
    assert entry.value == -1000
    assert entry.source == "set"
    assert entry.updated_at > 0


def test_record_notify_overwrites():
    cache = StateCache()
    cache.record_set("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)
    cache.record_notify("MIXER:Current/InCh/Fader/Level", 0, 0, -500)
    entry = cache.get("MIXER:Current/InCh/Fader/Level", 0, 0)
    assert entry.value == -500
    assert entry.source == "notify"


def test_mark_stale_flips_all_sources():
    cache = StateCache()
    cache.record_set("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)
    cache.record_init("MIXER:Current/InCh/Label/Name", 0, 0, "Kick")
    cache.mark_all_stale()
    assert cache.get("MIXER:Current/InCh/Fader/Level", 0, 0).source == "stale"
    assert cache.get("MIXER:Current/InCh/Label/Name", 0, 0).source == "stale"


def test_missing_entry_returns_none():
    cache = StateCache()
    assert cache.get("MIXER:Current/InCh/Fader/Level", 0, 0) is None
```

- [ ] **Step 2: Implementation**

```python
# src/dm3_mcp/state/cache.py
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

Source = Literal["init", "set", "notify", "stale"]


@dataclass
class CachedValue:
    value: int | str
    updated_at: float
    source: Source


class StateCache:
    def __init__(self) -> None:
        self._values: dict[tuple[str, int, int], CachedValue] = {}

    def _record(self, address: str, x: int, y: int, value: int | str, source: Source) -> None:
        self._values[(address, x, y)] = CachedValue(value=value, updated_at=time.monotonic(), source=source)

    def record_init(self, address: str, x: int, y: int, value: int | str) -> None:
        self._record(address, x, y, value, "init")

    def record_set(self, address: str, x: int, y: int, value: int | str) -> None:
        self._record(address, x, y, value, "set")

    def record_notify(self, address: str, x: int, y: int, value: int | str) -> None:
        self._record(address, x, y, value, "notify")

    def get(self, address: str, x: int, y: int) -> CachedValue | None:
        return self._values.get((address, x, y))

    def mark_all_stale(self) -> None:
        for key, val in self._values.items():
            self._values[key] = CachedValue(value=val.value, updated_at=val.updated_at, source="stale")
```

- [ ] **Step 3: Run**

```bash
.venv/bin/pytest tests/unit/test_cache.py -v
```

Expected: `4 passed`.

- [ ] **Step 4: Commit**

```bash
git add src/dm3_mcp/state/cache.py tests/unit/test_cache.py
git commit -m "feat(state): StateCache with set/notify/init source tagging"
```

---

### Task 16: Structured views (ChannelView, MixView)

**Files:**
- Modify: `src/dm3_mcp/state/cache.py`
- Modify: `tests/unit/test_cache.py`

- [ ] **Step 1: Failing test**

Append to `tests/unit/test_cache.py`:

```python
def test_channel_view_exposes_typed_getters():
    cache = StateCache()
    cache.record_set("MIXER:Current/InCh/Fader/Level", 4, 0, -500)  # ch 5 = -5 dB
    cache.record_set("MIXER:Current/InCh/Fader/On", 4, 0, 1)
    cache.record_set("MIXER:Current/InCh/Label/Name", 4, 0, "Snare")

    ch = cache.channel(5)
    assert ch.fader_db == -5.0
    assert ch.on is True
    assert ch.label == "Snare"
```

- [ ] **Step 2: Add two accessor methods to the existing `StateCache` class**

Open `src/dm3_mcp/state/cache.py` and add these methods inside the existing `StateCache` class (do NOT create a new class):

```python
    def channel(self, ch: int) -> "ChannelView":
        from .views import ChannelView
        return ChannelView(self, ch)

    def mix(self, mix_num: int) -> "MixView":
        from .views import MixView
        return MixView(self, mix_num)
```

- [ ] **Step 3: Create `src/dm3_mcp/state/views.py`**

```python
# src/dm3_mcp/state/views.py
from __future__ import annotations

from dm3_mcp.rcp.types import raw_to_db

from .cache import StateCache


class ChannelView:
    def __init__(self, cache: StateCache, ch_1based: int):
        self._cache = cache
        self._x = ch_1based - 1  # wire indexing is 0-based

    def _raw(self, address: str) -> int | str | None:
        entry = self._cache.get(address, self._x, 0)
        return entry.value if entry else None

    @property
    def fader_db(self) -> float | None:
        raw = self._raw("MIXER:Current/InCh/Fader/Level")
        return raw_to_db(int(raw)) if raw is not None else None

    @property
    def on(self) -> bool | None:
        raw = self._raw("MIXER:Current/InCh/Fader/On")
        return bool(raw) if raw is not None else None

    @property
    def label(self) -> str | None:
        raw = self._raw("MIXER:Current/InCh/Label/Name")
        return str(raw) if raw is not None else None

    @property
    def ha_gain_db(self) -> int | None:
        raw = self._cache.get("IO:Current/InCh/HAGain", self._x, 0)
        return int(raw.value) if raw else None

    @property
    def phantom_on(self) -> bool | None:
        raw = self._cache.get("IO:Current/InCh/48VOn", self._x, 0)
        return bool(raw.value) if raw else None


class MixView:
    def __init__(self, cache: StateCache, mix_1based: int):
        self._cache = cache
        self._x = mix_1based - 1

    @property
    def label(self) -> str | None:
        entry = self._cache.get("MIXER:Current/Mix/Label/Name", self._x, 0)
        return str(entry.value) if entry else None

    @property
    def fader_db(self) -> float | None:
        entry = self._cache.get("MIXER:Current/Mix/Fader/Level", self._x, 0)
        return raw_to_db(int(entry.value)) if entry else None
```

- [ ] **Step 4: Run**

```bash
.venv/bin/pytest tests/unit/test_cache.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dm3_mcp/state/cache.py src/dm3_mcp/state/views.py tests/unit/test_cache.py
git commit -m "feat(state): ChannelView and MixView typed accessors"
```

---

### Task 17: Wire NOTIFY handler to cache

**Files:**
- Create: `src/dm3_mcp/state/wiring.py`
- Create: `tests/unit/test_wiring.py`

- [ ] **Step 1: Write**

```python
# src/dm3_mcp/state/wiring.py
from dm3_mcp.rcp.client import RcpClient
from dm3_mcp.rcp.codec import ParsedResponse

from .cache import StateCache


def wire_cache_to_client(cache: StateCache, client: RcpClient) -> None:
    """Register a NOTIFY handler that mirrors all surface changes into the cache."""

    async def _on_notify(event: ParsedResponse) -> None:
        if event.address is None or event.x is None or event.y is None or event.value is None:
            return
        cache.record_notify(event.address, event.x, event.y, event.value)

    client.on_notify(_on_notify)
```

- [ ] **Step 2: Test**

```python
# tests/unit/test_wiring.py
import asyncio
import pytest

from dm3_mcp.rcp.client import RcpClient
from dm3_mcp.state.cache import StateCache
from dm3_mcp.state.wiring import wire_cache_to_client

from .test_client import FakeDM3


@pytest.mark.asyncio
async def test_notify_flows_into_cache():
    fake = FakeDM3({})

    async def _handle(reader, writer):
        await asyncio.sleep(0.05)
        writer.write(b"NOTIFY set MIXER:Current/InCh/Fader/Level 2 0 -700\n")
        await writer.drain()
        await asyncio.sleep(0.2)

    fake._handle = _handle  # type: ignore[assignment]
    await fake.start()

    try:
        cache = StateCache()
        client = RcpClient("127.0.0.1", fake.port)
        wire_cache_to_client(cache, client)
        await client.connect()
        await asyncio.sleep(0.2)
        await client.close()
        entry = cache.get("MIXER:Current/InCh/Fader/Level", 2, 0)
        assert entry is not None
        assert entry.value == -700
        assert entry.source == "notify"
    finally:
        await fake.stop()
```

- [ ] **Step 3: Run**

```bash
.venv/bin/pytest tests/unit/test_wiring.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/dm3_mcp/state/wiring.py tests/unit/test_wiring.py
git commit -m "feat(state): wire RcpClient NOTIFY events into StateCache"
```

---

### Task 18: Initial-sync sweep

**Files:**
- Create: `src/dm3_mcp/state/initial_sync.py`
- Modify: add coverage in `tests/unit/test_wiring.py`

- [ ] **Step 1: Write**

```python
# src/dm3_mcp/state/initial_sync.py
from __future__ import annotations

import asyncio
import logging

from dm3_mcp.rcp.client import RcpClient

from .cache import StateCache

log = logging.getLogger(__name__)


# Addresses to prime on connect. Format: (address, x_count, y_count)
# x_count/y_count control how many indices to fetch; use console capacities.
INITIAL_SYNC: list[tuple[str, int, int]] = [
    ("MIXER:Current/InCh/Fader/Level", 16, 1),
    ("MIXER:Current/InCh/Fader/On", 16, 1),
    ("MIXER:Current/InCh/Label/Name", 16, 1),
    ("IO:Current/InCh/HAGain", 16, 1),
    ("IO:Current/InCh/48VOn", 16, 1),
    ("MIXER:Current/StInCh/Fader/Level", 2, 1),
    ("MIXER:Current/StInCh/Fader/On", 2, 1),
    ("MIXER:Current/StInCh/Label/Name", 2, 1),
    ("MIXER:Current/Mix/Fader/Level", 6, 1),
    ("MIXER:Current/Mix/Fader/On", 6, 1),
    ("MIXER:Current/Mix/Label/Name", 6, 1),
    ("MIXER:Current/Mtrx/Fader/Level", 2, 1),
    ("MIXER:Current/Mtrx/Label/Name", 2, 1),
    ("MIXER:Current/St/Fader/Level", 1, 1),
    ("MIXER:Current/MuteGrpCtrl/On", 6, 1),
    ("MIXER:Current/MuteGrpCtrl/Label/Name", 6, 1),
]


async def run_initial_sync(client: RcpClient, cache: StateCache) -> int:
    """Fetch the priming subset of parameters. Returns count of entries written."""
    count = 0
    for address, x_max, y_max in INITIAL_SYNC:
        for x in range(x_max):
            for y in range(y_max):
                try:
                    result = await client.get(address, x, y)
                    if result.value is not None:
                        cache.record_init(address, x, y, result.value)
                        count += 1
                except Exception as e:  # noqa: BLE001
                    log.debug("initial sync skip %s[%d,%d]: %s", address, x, y, e)
    return count
```

- [ ] **Step 2: Commit (unit tests for this are covered by integration later)**

```bash
git add src/dm3_mcp/state/initial_sync.py
git commit -m "feat(state): initial sync sweep for priming cache on connect"
```

---

## Phase 4 (M3) — Primitive tools + safety layer

### Task 19: Safety helpers

**Files:**
- Create: `src/dm3_mcp/tools/safety.py`
- Create: `tests/unit/test_safety.py`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_safety.py
import pytest

from dm3_mcp.tools.safety import FaderLimitExceeded, SafetyContext


def test_preview_mode_blocks_send():
    ctx = SafetyContext(mode="preview", max_fader_db=6.0)
    assert ctx.should_send() is False


def test_live_mode_allows_send():
    ctx = SafetyContext(mode="live", max_fader_db=6.0)
    assert ctx.should_send() is True


def test_limited_mode_rejects_above_clamp():
    ctx = SafetyContext(mode="limited", max_fader_db=6.0)
    with pytest.raises(FaderLimitExceeded):
        ctx.check_fader_db(7.0)


def test_limited_mode_allows_at_clamp():
    ctx = SafetyContext(mode="limited", max_fader_db=6.0)
    ctx.check_fader_db(6.0)  # no raise


def test_override_bypasses_clamp():
    ctx = SafetyContext(mode="limited", max_fader_db=6.0)
    ctx.check_fader_db(10.0, override=True)  # no raise
```

- [ ] **Step 2: Implementation**

```python
# src/dm3_mcp/tools/safety.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["preview", "live", "limited"]


class FaderLimitExceeded(Exception):
    pass


class ReadOnlyAddress(Exception):
    pass


class PreviewOnly(Exception):
    pass


@dataclass
class SafetyContext:
    mode: Mode = "limited"
    max_fader_db: float = 6.0

    def should_send(self) -> bool:
        return self.mode != "preview"

    def check_fader_db(self, db: float, *, override: bool = False) -> None:
        if override:
            return
        if self.mode == "limited" and db > self.max_fader_db:
            raise FaderLimitExceeded(
                f"requested {db} dB exceeds max_fader_db={self.max_fader_db}; "
                "pass override_safety=True to force"
            )
```

- [ ] **Step 3: Run**

```bash
.venv/bin/pytest tests/unit/test_safety.py -v
```

Expected: `5 passed`.

- [ ] **Step 4: Commit**

```bash
git add src/dm3_mcp/tools/safety.py tests/unit/test_safety.py
git commit -m "feat(safety): mode switch + fader dB clamp"
```

---

### Task 20: FastMCP server skeleton + `connect_console`

**Files:**
- Create: `src/dm3_mcp/config.py`
- Create: `src/dm3_mcp/server.py`

- [ ] **Step 1: Config**

```python
# src/dm3_mcp/config.py
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_scenes_file() -> Path:
    base = os.environ.get("DM3_MCP_SCENES_FILE")
    if base:
        return Path(base)
    return Path.home() / ".dm3-mcp" / "scenes.json"


@dataclass
class Config:
    host: str = os.environ.get("DM3_HOST", "192.168.0.128")
    port: int = int(os.environ.get("DM3_PORT", "49280"))
    max_fader_db: float = float(os.environ.get("DM3_MAX_FADER_DB", "6.0"))
    scenes_file: Path = field(default_factory=_default_scenes_file)
```

- [ ] **Step 2: Server skeleton**

```python
# src/dm3_mcp/server.py
"""FastMCP server entrypoint for the Yamaha DM3 MCP."""
from __future__ import annotations

import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from .config import Config
from .rcp.client import RcpClient
from .state.cache import StateCache
from .state.initial_sync import run_initial_sync
from .state.wiring import wire_cache_to_client
from .tools.safety import SafetyContext

log = logging.getLogger(__name__)

mcp = FastMCP("yamaha-dm3")
_config = Config()
_client: RcpClient | None = None
_cache = StateCache()
_safety = SafetyContext(max_fader_db=_config.max_fader_db)


@mcp.tool()
async def connect_console(host: str | None = None, port: int = 49280) -> dict:
    """Open a persistent TCP connection to the DM3 and prime the state cache.

    Args:
        host: IP address of the DM3. If omitted, uses DM3_HOST env or 192.168.0.128.
        port: TCP port (default 49280).

    Returns:
        {ok, host, port, initial_sync_count}
    """
    global _client
    effective_host = host or _config.host
    _client = RcpClient(effective_host, port)
    wire_cache_to_client(_cache, _client)
    await _client.connect()
    count = await run_initial_sync(_client, _cache)
    return {"ok": True, "host": effective_host, "port": port, "initial_sync_count": count}


@mcp.tool()
async def disconnect_console() -> dict:
    """Close the DM3 connection cleanly."""
    global _client
    if _client is None:
        return {"ok": True, "already_disconnected": True}
    await _client.close()
    _client = None
    _cache.mark_all_stale()
    return {"ok": True}


@mcp.tool()
async def get_connection_status() -> dict:
    """Return the current connection state and a lightweight cache summary."""
    return {
        "ok": True,
        "connected": _client is not None,
        "host": _config.host if _client else None,
        "cache_entries": len(_cache._values),
    }


@mcp.tool()
async def set_safety_mode(mode: str) -> dict:
    """Set the global safety mode.

    Args:
        mode: 'preview' (log only), 'live' (no clamp), 'limited' (clamp to max_fader_db).
    """
    if mode not in ("preview", "live", "limited"):
        return {"ok": False, "error": {"code": "bad_mode", "message": f"unknown mode {mode!r}"}}
    _safety.mode = mode  # type: ignore[assignment]
    return {"ok": True, "mode": mode}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke-test that the server module imports**

```bash
.venv/bin/python -c "from dm3_mcp.server import mcp; print(list(mcp._tool_manager.list_tools()))"
```

Expected: prints a list containing `connect_console`, `disconnect_console`, etc.

- [ ] **Step 4: Commit**

```bash
git add src/dm3_mcp/config.py src/dm3_mcp/server.py
git commit -m "feat(mcp): server skeleton with connect/disconnect/status/safety tools"
```

---

### Task 21: Read tools — `get_channel_state`, `get_mix_state`, `get_all_labels`

**Files:**
- Modify: `src/dm3_mcp/server.py`
- Create: `tests/unit/test_read_tools.py`

- [ ] **Step 1: Add tool definitions to `server.py` (append)**

```python
@mcp.tool()
async def get_channel_state(ch_num: int) -> dict:
    """Return cached state for an input channel (1–16).

    Args:
        ch_num: 1-based input channel number.
    """
    if not 1 <= ch_num <= 16:
        return {"ok": False, "error": {"code": "bad_channel", "message": f"ch {ch_num} out of range 1-16"}}
    view = _cache.channel(ch_num)
    entry_level = _cache.get("MIXER:Current/InCh/Fader/Level", ch_num - 1, 0)
    stale = entry_level is not None and entry_level.source == "stale"
    return {
        "ok": True,
        "stale": stale,
        "channel": ch_num,
        "label": view.label,
        "fader_db": view.fader_db,
        "on": view.on,
        "ha_gain_db": view.ha_gain_db,
        "phantom_on": view.phantom_on,
    }


@mcp.tool()
async def get_mix_state(mix_num: int) -> dict:
    """Return cached state for a mix bus (1–6)."""
    if not 1 <= mix_num <= 6:
        return {"ok": False, "error": {"code": "bad_mix", "message": f"mix {mix_num} out of range 1-6"}}
    view = _cache.mix(mix_num)
    return {"ok": True, "mix": mix_num, "label": view.label, "fader_db": view.fader_db}


@mcp.tool()
async def get_all_labels() -> dict:
    """Return a compact overview of all labeled channels and mixes."""
    inch = {}
    for ch in range(1, 17):
        view = _cache.channel(ch)
        if view.label is not None:
            inch[ch] = view.label
    mixes = {}
    for m in range(1, 7):
        view = _cache.mix(m)
        if view.label is not None:
            mixes[m] = view.label
    return {"ok": True, "inch": inch, "mix": mixes}
```

- [ ] **Step 2: Test with a prepopulated cache**

```python
# tests/unit/test_read_tools.py
import pytest

from dm3_mcp.server import _cache, get_all_labels, get_channel_state


@pytest.mark.asyncio
async def test_get_channel_state_bad_range():
    result = await get_channel_state(0)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_get_channel_state_returns_label_and_fader():
    _cache.record_set("MIXER:Current/InCh/Label/Name", 0, 0, "Kick")
    _cache.record_set("MIXER:Current/InCh/Fader/Level", 0, 0, -500)
    _cache.record_set("MIXER:Current/InCh/Fader/On", 0, 0, 1)
    result = await get_channel_state(1)
    assert result["ok"] is True
    assert result["label"] == "Kick"
    assert result["fader_db"] == -5.0
    assert result["on"] is True


@pytest.mark.asyncio
async def test_get_all_labels_includes_populated():
    _cache.record_set("MIXER:Current/InCh/Label/Name", 1, 0, "Snare")
    result = await get_all_labels()
    assert result["ok"] is True
    assert 2 in result["inch"]
    assert result["inch"][2] == "Snare"
```

> **Note:** These tests share the module-level `_cache`. In a later refactor we can parameterize it; for now order-independence is maintained because each test seeds only what it reads.

- [ ] **Step 3: Run**

```bash
.venv/bin/pytest tests/unit/test_read_tools.py -v
```

Expected: `3 passed`.

- [ ] **Step 4: Commit**

```bash
git add src/dm3_mcp/server.py tests/unit/test_read_tools.py
git commit -m "feat(mcp): read tools — channel state, mix state, all labels"
```

---

### Task 22: `get_mute_group_states`, `get_current_scene`, `read_meter`

**Files:**
- Modify: `src/dm3_mcp/server.py`

- [ ] **Step 1: Append to `server.py`**

```python
@mcp.tool()
async def get_mute_group_states() -> dict:
    """Return on/off and label for all 6 mute groups."""
    groups = {}
    for g in range(1, 7):
        on_entry = _cache.get("MIXER:Current/MuteGrpCtrl/On", g - 1, 0)
        label_entry = _cache.get("MIXER:Current/MuteGrpCtrl/Label/Name", g - 1, 0)
        groups[g] = {
            "on": bool(on_entry.value) if on_entry else None,
            "label": label_entry.value if label_entry else None,
        }
    return {"ok": True, "groups": groups}


@mcp.tool()
async def get_current_scene() -> dict:
    """Ask the console for the current scene and enrich with local metadata.

    Returns {bank, number, name_from_metadata (or None)}.
    """
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected", "message": "call connect_console first"}}
    # Query current scene for both banks; take the most recently used (non-empty)
    try:
        a = await _client._send("sscurrent_ex scene_a\n", timeout=2.0)
        b = await _client._send("sscurrent_ex scene_b\n", timeout=2.0)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": {"code": "rcp_error", "message": str(e)}}
    return {"ok": True, "a": a.raw, "b": b.raw}


@mcp.tool()
async def read_meter(target: str, num: int, pickoff: str = "PostOn") -> dict:
    """Read a live meter value. Bypasses the cache.

    Args:
        target: 'inch' | 'mix' | 'mtrx' | 'st'
        num: 1-based channel/bus number
        pickoff: 'PreHPF' | 'PreEQ' | 'PreFader' | 'PostOn' (valid set depends on target)
    """
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected", "message": ""}}
    address_map = {
        "inch": "MIXER:Current/Meter/InCh",
        "mix": "MIXER:Current/Meter/Mix",
        "mtrx": "MIXER:Current/Meter/Mtrx",
        "st": "MIXER:Current/Meter/St",
    }
    if target not in address_map:
        return {"ok": False, "error": {"code": "bad_target", "message": f"unknown {target!r}"}}
    pickoff_map = {"PreHPF": 1, "PreEQ": 1, "PreFader": 2, "PostOn": 3}
    y = pickoff_map.get(pickoff)
    if y is None:
        return {"ok": False, "error": {"code": "bad_pickoff", "message": pickoff}}
    try:
        result = await _client.get(address_map[target], num - 1, y)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": {"code": "rcp_error", "message": str(e)}}
    return {"ok": True, "target": target, "num": num, "pickoff": pickoff, "value": result.value}
```

- [ ] **Step 2: Commit**

```bash
git add src/dm3_mcp/server.py
git commit -m "feat(mcp): read tools — mute group states, current scene, read meter"
```

---

### Task 23: Write primitives — labels

**Files:**
- Modify: `src/dm3_mcp/server.py`
- Create: `tests/unit/test_write_primitives.py`

- [ ] **Step 1: Append to `server.py`**

```python
from enum import Enum


class ChType(str, Enum):
    inch = "inch"
    stinch = "stinch"
    fxrtn = "fxrtn"
    mix = "mix"
    matrix = "matrix"
    stereo = "stereo"
    fx = "fx"


_LABEL_NAME_ADDRESS = {
    "inch": "MIXER:Current/InCh/Label/Name",
    "stinch": "MIXER:Current/StInCh/Label/Name",
    "fxrtn": "MIXER:Current/FxRtnCh/Label/Name",
    "mix": "MIXER:Current/Mix/Label/Name",
    "matrix": "MIXER:Current/Mtrx/Label/Name",
    "stereo": "MIXER:Current/St/Label/Name",
    "fx": "MIXER:Current/Fx/Label/Name",
}


@mcp.tool()
async def set_channel_label(
    ch_type: str,
    ch_num: int,
    name: str | None = None,
    color: str | None = None,
    icon: str | None = None,
    category: str | None = None,
) -> dict:
    """Set any subset of label fields for a channel/bus.

    Args:
        ch_type: one of 'inch','stinch','fxrtn','mix','matrix','stereo','fx'
        ch_num: 1-based index
        name: up to 8 chars
        color: 'Blue','Green','Orange','Pink','Purple','Red','SkyBlue','Yellow','Cyan','Magenta','Off'
        icon: one of the DM3 icon names (e.g., 'Kick','Vocal','Piano')
        category: from Table 5 (input) or Table 6 (output)
    """
    if ch_type not in _LABEL_NAME_ADDRESS:
        return {"ok": False, "error": {"code": "bad_ch_type", "message": ch_type}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    applied: dict[str, str] = {}
    for field, value in (("Name", name), ("Color", color), ("Icon", icon), ("Category", category)):
        if value is None:
            continue
        base = _LABEL_NAME_ADDRESS[ch_type].rsplit("/", 1)[0]
        address = f"{base}/{field}"
        if _safety.should_send():
            await _client.set(address, ch_num - 1, 0, value)
            _cache.record_set(address, ch_num - 1, 0, value)
        applied[field.lower()] = value
    return {"ok": True, "ch_type": ch_type, "ch_num": ch_num, "applied": applied}
```

- [ ] **Step 2: Test with mocked client**

```python
# tests/unit/test_write_primitives.py
import pytest
from unittest.mock import AsyncMock

import dm3_mcp.server as srv


@pytest.mark.asyncio
async def test_set_channel_label_name(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)

    result = await srv.set_channel_label("inch", 1, name="Kick")
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with(
        "MIXER:Current/InCh/Label/Name", 0, 0, "Kick"
    )


@pytest.mark.asyncio
async def test_set_channel_label_multiple(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)

    result = await srv.set_channel_label("inch", 3, name="Snare", color="Red")
    assert result["ok"] is True
    assert mock_client.set.await_count == 2
```

- [ ] **Step 3: Run**

```bash
.venv/bin/pytest tests/unit/test_write_primitives.py -v
```

Expected: `2 passed`.

- [ ] **Step 4: Commit**

```bash
git add src/dm3_mcp/server.py tests/unit/test_write_primitives.py
git commit -m "feat(mcp): primitive — set_channel_label with subset updates"
```

---

### Task 24: Write primitives — fader level + channel on

**Files:**
- Modify: `src/dm3_mcp/server.py`
- Modify: `tests/unit/test_write_primitives.py`

- [ ] **Step 1: Append to `server.py`**

```python
from .rcp.types import NEG_INF_RAW, db_to_raw


_FADER_LEVEL_ADDRESS = {
    "inch": "MIXER:Current/InCh/Fader/Level",
    "stinch": "MIXER:Current/StInCh/Fader/Level",
    "fxrtn": "MIXER:Current/FxRtnCh/Fader/Level",
    "mix": "MIXER:Current/Mix/Fader/Level",
    "matrix": "MIXER:Current/Mtrx/Fader/Level",
    "stereo": "MIXER:Current/St/Fader/Level",
    "fx": "MIXER:Current/Fx/Fader/Level",
}

_FADER_ON_ADDRESS = {
    k: v.replace("/Fader/Level", "/Fader/On") for k, v in _FADER_LEVEL_ADDRESS.items()
}


@mcp.tool()
async def set_fader_level(
    target_type: str,
    target_num: int,
    level_db: float,
    override_safety: bool = False,
) -> dict:
    """Set a fader level in dB. Pass float('-inf') for -∞.

    Clamped to max_fader_db in 'limited' safety mode.
    """
    if target_type not in _FADER_LEVEL_ADDRESS:
        return {"ok": False, "error": {"code": "bad_target_type", "message": target_type}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    try:
        _safety.check_fader_db(level_db if level_db != float("-inf") else -999, override=override_safety)
        raw = db_to_raw(level_db) if level_db != float("-inf") else NEG_INF_RAW
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": {"code": "safety", "message": str(e)}}
    address = _FADER_LEVEL_ADDRESS[target_type]
    if _safety.should_send():
        await _client.set(address, target_num - 1, 0, raw)
        _cache.record_set(address, target_num - 1, 0, raw)
    return {"ok": True, "target_type": target_type, "target_num": target_num, "level_db": level_db}


@mcp.tool()
async def set_channel_on(target_type: str, target_num: int, on: bool) -> dict:
    """Channel On switch. Yamaha semantics: on=audible."""
    if target_type not in _FADER_ON_ADDRESS:
        return {"ok": False, "error": {"code": "bad_target_type", "message": target_type}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    address = _FADER_ON_ADDRESS[target_type]
    raw = 1 if on else 0
    if _safety.should_send():
        await _client.set(address, target_num - 1, 0, raw)
        _cache.record_set(address, target_num - 1, 0, raw)
    return {"ok": True, "target_type": target_type, "target_num": target_num, "on": on}
```

- [ ] **Step 2: Tests — append to `tests/unit/test_write_primitives.py`**

```python
@pytest.mark.asyncio
async def test_set_fader_level_normal(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    srv._safety.mode = "limited"

    result = await srv.set_fader_level("inch", 1, -10.0)
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)


@pytest.mark.asyncio
async def test_set_fader_level_above_clamp_rejected(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    srv._safety.mode = "limited"
    srv._safety.max_fader_db = 6.0

    result = await srv.set_fader_level("inch", 1, 10.0)
    assert result["ok"] is False
    assert result["error"]["code"] == "safety"
    mock_client.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_fader_level_override(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    srv._safety.mode = "limited"
    srv._safety.max_fader_db = 6.0

    result = await srv.set_fader_level("inch", 1, 10.0, override_safety=True)
    assert result["ok"] is True
    mock_client.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_fader_neg_inf(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)

    result = await srv.set_fader_level("inch", 1, float("-inf"))
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("MIXER:Current/InCh/Fader/Level", 0, 0, -32768)


@pytest.mark.asyncio
async def test_set_channel_on(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)

    result = await srv.set_channel_on("inch", 2, False)
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("MIXER:Current/InCh/Fader/On", 1, 0, 0)
```

- [ ] **Step 3: Run**

```bash
.venv/bin/pytest tests/unit/test_write_primitives.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/dm3_mcp/server.py tests/unit/test_write_primitives.py
git commit -m "feat(mcp): primitives — set_fader_level with clamp, set_channel_on"
```

---

### Task 25: Mute groups + head amp + phantom

**Files:**
- Modify: `src/dm3_mcp/server.py`
- Modify: `tests/unit/test_write_primitives.py`

- [ ] **Step 1: Append to `server.py`**

```python
@mcp.tool()
async def set_mute_group(group_num: int, active: bool) -> dict:
    """Activate or release a mute group (1–6). Active=True means the group is muting."""
    if not 1 <= group_num <= 6:
        return {"ok": False, "error": {"code": "bad_group", "message": f"{group_num}"}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    raw = 1 if active else 0
    address = "MIXER:Current/MuteGrpCtrl/On"
    if _safety.should_send():
        await _client.set(address, group_num - 1, 0, raw)
        _cache.record_set(address, group_num - 1, 0, raw)
    return {"ok": True, "group": group_num, "active": active}


@mcp.tool()
async def set_mute_group_label(group_num: int, name: str) -> dict:
    """Rename a mute group."""
    if not 1 <= group_num <= 6:
        return {"ok": False, "error": {"code": "bad_group"}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    address = "MIXER:Current/MuteGrpCtrl/Label/Name"
    if _safety.should_send():
        await _client.set(address, group_num - 1, 0, name)
        _cache.record_set(address, group_num - 1, 0, name)
    return {"ok": True, "group": group_num, "name": name}


@mcp.tool()
async def set_head_amp_gain(input_ch: int, gain_db: int) -> dict:
    """Set analog head-amp gain on an input channel (0–64 dB, integer)."""
    if not 1 <= input_ch <= 16:
        return {"ok": False, "error": {"code": "bad_channel"}}
    if not 0 <= gain_db <= 64:
        return {"ok": False, "error": {"code": "bad_gain", "message": "0..64 dB"}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    address = "IO:Current/InCh/HAGain"
    if _safety.should_send():
        await _client.set(address, input_ch - 1, 0, gain_db)
        _cache.record_set(address, input_ch - 1, 0, gain_db)
    return {"ok": True, "input_ch": input_ch, "gain_db": gain_db}


@mcp.tool()
async def set_phantom_power(input_ch: int, on: bool) -> dict:
    """Toggle 48V phantom power on an input channel."""
    if not 1 <= input_ch <= 16:
        return {"ok": False, "error": {"code": "bad_channel"}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    address = "IO:Current/InCh/48VOn"
    raw = 1 if on else 0
    if _safety.should_send():
        await _client.set(address, input_ch - 1, 0, raw)
        _cache.record_set(address, input_ch - 1, 0, raw)
    return {"ok": True, "input_ch": input_ch, "on": on}
```

- [ ] **Step 2: Test (append)**

```python
@pytest.mark.asyncio
async def test_set_mute_group(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_mute_group(3, True)
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("MIXER:Current/MuteGrpCtrl/On", 2, 0, 1)


@pytest.mark.asyncio
async def test_set_head_amp_gain(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_head_amp_gain(5, 30)
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("IO:Current/InCh/HAGain", 4, 0, 30)


@pytest.mark.asyncio
async def test_set_head_amp_gain_out_of_range(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_head_amp_gain(1, 70)
    assert result["ok"] is False
    mock_client.set.assert_not_awaited()
```

- [ ] **Step 3: Run & commit**

```bash
.venv/bin/pytest tests/unit/test_write_primitives.py -v
git add src/dm3_mcp/server.py tests/unit/test_write_primitives.py
git commit -m "feat(mcp): primitives — mute groups, head amp gain, phantom power"
```

---

### Task 26: Unified `set_send`

**Files:**
- Modify: `src/dm3_mcp/server.py`
- Modify: `tests/unit/test_write_primitives.py`

- [ ] **Step 1: Append to `server.py`**

```python
# Maps (from_type, to_type) -> base address like ".../InCh/ToMix"
_SEND_BASE = {
    ("inch", "mix"): "MIXER:Current/InCh/ToMix",
    ("inch", "matrix"): "MIXER:Current/InCh/ToMtrx",
    ("inch", "fx"): "MIXER:Current/InCh/ToFx",
    ("inch", "stereo"): "MIXER:Current/InCh/ToSt",
    ("stinch", "mix"): "MIXER:Current/StInCh/ToMix",
    ("stinch", "matrix"): "MIXER:Current/StInCh/ToMtrx",
    ("stinch", "fx"): "MIXER:Current/StInCh/ToFx",
    ("stinch", "stereo"): "MIXER:Current/StInCh/ToSt",
    ("fxrtn", "mix"): "MIXER:Current/FxRtnCh/ToMix",
    ("fxrtn", "matrix"): "MIXER:Current/FxRtnCh/ToMtrx",
    ("fxrtn", "stereo"): "MIXER:Current/FxRtnCh/ToSt",
    ("mix", "matrix"): "MIXER:Current/Mix/ToMtrx",
    ("mix", "stereo"): "MIXER:Current/Mix/ToSt",
}


@mcp.tool()
async def set_send(
    from_type: str,
    from_num: int,
    to_type: str,
    to_num: int,
    level_db: float | None = None,
    on: bool | None = None,
    pan: int | None = None,
    prepost: str | None = None,  # "pre" or "post"
) -> dict:
    """Update a send atomically. Any subset of level/on/pan/prepost in one call.

    Indices are 1-based. `to_num` is ignored when to_type='stereo' (single pair).
    """
    key = (from_type, to_type)
    if key not in _SEND_BASE:
        return {"ok": False, "error": {"code": "bad_route", "message": f"{from_type}->{to_type}"}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    base = _SEND_BASE[key]
    x = from_num - 1
    y = 0 if to_type == "stereo" else to_num - 1
    applied = {}

    async def _apply(suffix: str, value):
        address = f"{base}/{suffix}"
        if _safety.should_send():
            await _client.set(address, x, y, value)
            _cache.record_set(address, x, y, value)
        applied[suffix.lower()] = value

    if level_db is not None:
        raw = db_to_raw(level_db) if level_db != float("-inf") else NEG_INF_RAW
        await _apply("Level", raw)
    if on is not None:
        await _apply("On", 1 if on else 0)
    if pan is not None:
        if not -63 <= pan <= 63:
            return {"ok": False, "error": {"code": "bad_pan"}}
        await _apply("Pan", pan)
    if prepost is not None:
        if prepost not in ("pre", "post"):
            return {"ok": False, "error": {"code": "bad_prepost"}}
        await _apply("PrePost", 0 if prepost == "pre" else 1)

    return {"ok": True, "applied": applied, "from": [from_type, from_num], "to": [to_type, to_num]}
```

- [ ] **Step 2: Test**

Append to `tests/unit/test_write_primitives.py`:

```python
@pytest.mark.asyncio
async def test_set_send_level_and_on(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_send("inch", 1, "mix", 1, level_db=0.0, on=True)
    assert result["ok"] is True
    assert mock_client.set.await_count == 2


@pytest.mark.asyncio
async def test_set_send_unknown_route(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_send("fx", 1, "mix", 1, level_db=0.0)
    assert result["ok"] is False
```

- [ ] **Step 3: Run & commit**

```bash
.venv/bin/pytest tests/unit/test_write_primitives.py -v
git add src/dm3_mcp/server.py tests/unit/test_write_primitives.py
git commit -m "feat(mcp): primitive — unified set_send with atomic subset updates"
```

---

### Task 27: HPF, PEQ, channel link

**Files:**
- Modify: `src/dm3_mcp/server.py`

- [ ] **Step 1: Append**

```python
_HPF_ON_ADDRESS = {
    "inch": "MIXER:Current/InCh/HPF/On",
    "mix": "MIXER:Current/Mix/HPF/On",
    "mtrx": "MIXER:Current/Mtrx/HPF/On",
}

_PEQ_BASE = {
    "inch": "MIXER:Current/InCh/PEQ",
    "mix": "MIXER:Current/Mix/PEQ",
    "mtrx": "MIXER:Current/Mtrx/PEQ",
}


@mcp.tool()
async def set_hpf(ch_type: str, ch_num: int, on: bool, freq_hz: int | None = None) -> dict:
    """Toggle HPF and optionally set cutoff."""
    if ch_type not in _HPF_ON_ADDRESS:
        return {"ok": False, "error": {"code": "bad_ch_type"}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    on_addr = _HPF_ON_ADDRESS[ch_type]
    if _safety.should_send():
        await _client.set(on_addr, ch_num - 1, 0, 1 if on else 0)
        _cache.record_set(on_addr, ch_num - 1, 0, 1 if on else 0)
    applied = {"on": on}
    # Freq addressing mirrors RCP: .../HPF/Freq per channel. Verified in M0.
    if freq_hz is not None:
        freq_addr = on_addr.replace("/On", "/Freq")
        if _safety.should_send():
            await _client.set(freq_addr, ch_num - 1, 0, freq_hz)
            _cache.record_set(freq_addr, ch_num - 1, 0, freq_hz)
        applied["freq_hz"] = freq_hz
    return {"ok": True, "ch_type": ch_type, "ch_num": ch_num, "applied": applied}


@mcp.tool()
async def set_peq_band(
    ch_type: str,
    ch_num: int,
    band: int,
    freq_hz: int | None = None,
    gain_db: float | None = None,
    q: int | None = None,
    type_: str | None = None,
) -> dict:
    """Configure a PEQ band (1–4). Any subset of freq/gain/q/type updates."""
    if ch_type not in _PEQ_BASE:
        return {"ok": False, "error": {"code": "bad_ch_type"}}
    if not 1 <= band <= 4:
        return {"ok": False, "error": {"code": "bad_band"}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    base = _PEQ_BASE[ch_type]
    applied = {}

    async def _apply(suffix: str, value):
        address = f"{base}/Band/{suffix}"
        if _safety.should_send():
            await _client.set(address, ch_num - 1, band - 1, value)
            _cache.record_set(address, ch_num - 1, band - 1, value)
        applied[suffix.lower()] = value

    if freq_hz is not None:
        await _apply("Freq", freq_hz)
    if gain_db is not None:
        await _apply("Gain", int(round(gain_db * 100)))
    if q is not None:
        await _apply("Q", q)
    if type_ is not None:
        await _apply("Type", type_)
    return {"ok": True, "applied": applied}


_LINK_GROUP_ADDRESS = {
    "inch": "MIXER:Current/InputChLink/InCh/Assign",
    "stinch": "MIXER:Current/InputChLink/StInCh/Assign",
    "fxrtn": "MIXER:Current/InputChLink/FxRtnCh/Assign",
}


@mcp.tool()
async def set_channel_link_group(input_type: str, ch_num: int, group: int) -> dict:
    """Assign a channel to an input-link group. group=0 for NONE, 1–9 for A–I."""
    if input_type not in _LINK_GROUP_ADDRESS:
        return {"ok": False, "error": {"code": "bad_input_type"}}
    if not 0 <= group <= 9:
        return {"ok": False, "error": {"code": "bad_group"}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    address = _LINK_GROUP_ADDRESS[input_type]
    if _safety.should_send():
        await _client.set(address, ch_num - 1, 0, group)
        _cache.record_set(address, ch_num - 1, 0, group)
    return {"ok": True, "input_type": input_type, "ch_num": ch_num, "group": group}
```

- [ ] **Step 2: Commit**

```bash
git add src/dm3_mcp/server.py
git commit -m "feat(mcp): primitives — set_hpf, set_peq_band, set_channel_link_group"
```

---

### Task 28: `emergency_mute_all`

**Files:**
- Modify: `src/dm3_mcp/server.py`
- Modify: `tests/unit/test_write_primitives.py`

- [ ] **Step 1: Append to `server.py`**

```python
@mcp.tool()
async def emergency_mute_all(on: bool = True) -> dict:
    """Mute (on=True) or unmute (on=False) every input channel immediately.

    Bypasses safety-mode preview — always attempts to send when connected.
    """
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    raw = 0 if on else 1  # 'on' in Yamaha = audible; we want to mute -> write 0
    address = "MIXER:Current/InCh/Fader/On"
    muted: list[int] = []
    for x in range(16):
        await _client.set(address, x, 0, raw)
        _cache.record_set(address, x, 0, raw)
        muted.append(x + 1)
    # Also stereo inputs and FX returns
    for addr in (
        "MIXER:Current/StInCh/Fader/On",
        "MIXER:Current/FxRtnCh/Fader/On",
    ):
        count = 2 if "StInCh" in addr else 4
        for x in range(count):
            await _client.set(addr, x, 0, raw)
            _cache.record_set(addr, x, 0, raw)
    return {"ok": True, "muted_on": on, "inch_count": len(muted)}
```

- [ ] **Step 2: Test**

```python
@pytest.mark.asyncio
async def test_emergency_mute_all(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.emergency_mute_all(True)
    assert result["ok"] is True
    # 16 input + 2 stereo + 4 FX = 22 writes
    assert mock_client.set.await_count == 22
```

- [ ] **Step 3: Run & commit**

```bash
.venv/bin/pytest tests/unit/test_write_primitives.py -v
git add src/dm3_mcp/server.py tests/unit/test_write_primitives.py
git commit -m "feat(mcp): emergency_mute_all — bulk channel mute across input strip"
```

---

## Phase 5 (M4) — Macro tools + scene metadata + scene tools

### Task 29: Macro — `set_mix_exclusive_inputs`

**Files:**
- Modify: `src/dm3_mcp/server.py`
- Create: `tests/unit/test_macros.py`

- [ ] **Step 1: Append to `server.py`**

```python
@mcp.tool()
async def set_mix_exclusive_inputs(
    mix_num: int,
    input_channels: list[int],
    level_db: float = 0.0,
) -> dict:
    """Turn ALL input sends to this mix off, except the listed ones (which are turned on).

    Optionally sets each enabled send's level to `level_db`.
    """
    if not 1 <= mix_num <= 6:
        return {"ok": False, "error": {"code": "bad_mix"}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    enabled: list[int] = []
    disabled: list[int] = []
    failed: list[tuple[int, str]] = []
    on_addr = "MIXER:Current/InCh/ToMix/On"
    lvl_addr = "MIXER:Current/InCh/ToMix/Level"
    raw_level = db_to_raw(level_db) if level_db != float("-inf") else NEG_INF_RAW
    for ch in range(1, 17):
        want_on = ch in input_channels
        try:
            if _safety.should_send():
                await _client.set(on_addr, ch - 1, mix_num - 1, 1 if want_on else 0)
                _cache.record_set(on_addr, ch - 1, mix_num - 1, 1 if want_on else 0)
                if want_on:
                    await _client.set(lvl_addr, ch - 1, mix_num - 1, raw_level)
                    _cache.record_set(lvl_addr, ch - 1, mix_num - 1, raw_level)
            (enabled if want_on else disabled).append(ch)
        except Exception as e:  # noqa: BLE001
            failed.append((ch, str(e)))
    return {
        "ok": True,
        "mix": mix_num,
        "enabled": enabled,
        "disabled": disabled,
        "failed": failed,
        "level_db": level_db,
    }
```

- [ ] **Step 2: Test**

```python
# tests/unit/test_macros.py
import pytest
from unittest.mock import AsyncMock

import dm3_mcp.server as srv


@pytest.mark.asyncio
async def test_set_mix_exclusive_inputs_enables_two_disables_rest(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    srv._safety.mode = "limited"

    result = await srv.set_mix_exclusive_inputs(1, [1, 2], level_db=-3.0)
    assert result["ok"] is True
    assert result["enabled"] == [1, 2]
    assert result["disabled"] == [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
    # 16 On writes + 2 Level writes = 18 total
    assert mock_client.set.await_count == 18
```

- [ ] **Step 3: Run & commit**

```bash
.venv/bin/pytest tests/unit/test_macros.py -v
git add src/dm3_mcp/server.py tests/unit/test_macros.py
git commit -m "feat(mcp): macro — set_mix_exclusive_inputs"
```

---

### Task 30: Macros — `label_channels`, `apply_channel_preset`, `configure_mix_bus`

**Files:**
- Modify: `src/dm3_mcp/server.py`
- Modify: `tests/unit/test_macros.py`

- [ ] **Step 1: Append to `server.py`**

```python
@mcp.tool()
async def label_channels(mapping: dict) -> dict:
    """Bulk-label inputs. Keys are channel numbers (as strings since JSON); values are dicts.

    Example: {"1": {"name": "Kick"}, "2": {"name": "Snare", "color": "Red"}}
    """
    results = {}
    for k, v in mapping.items():
        ch_num = int(k)
        r = await set_channel_label("inch", ch_num, **v)  # type: ignore[arg-type]
        results[ch_num] = r
    return {"ok": True, "results": results}


@mcp.tool()
async def apply_channel_preset(ch_num: int, preset: dict) -> dict:
    """Apply a whole-channel configuration in one call.

    preset keys (all optional): name, color, icon, category, ha_gain_db, phantom_on,
        hpf_on, hpf_hz, fader_db, on.
    """
    steps = []
    if any(k in preset for k in ("name", "color", "icon", "category")):
        steps.append(await set_channel_label(
            "inch", ch_num,
            name=preset.get("name"),
            color=preset.get("color"),
            icon=preset.get("icon"),
            category=preset.get("category"),
        ))
    if "ha_gain_db" in preset:
        steps.append(await set_head_amp_gain(ch_num, preset["ha_gain_db"]))
    if "phantom_on" in preset:
        steps.append(await set_phantom_power(ch_num, preset["phantom_on"]))
    if "hpf_on" in preset:
        steps.append(await set_hpf("inch", ch_num, preset["hpf_on"], preset.get("hpf_hz")))
    if "fader_db" in preset:
        steps.append(await set_fader_level("inch", ch_num, preset["fader_db"]))
    if "on" in preset:
        steps.append(await set_channel_on("inch", ch_num, preset["on"]))
    return {"ok": True, "ch_num": ch_num, "steps": steps}


@mcp.tool()
async def configure_mix_bus(
    mix_num: int,
    name: str | None = None,
    fader_db: float | None = None,
    exclusive_inputs: list[int] | None = None,
    send_level_db: float = 0.0,
) -> dict:
    """Name a mix, set its fader, and exclusively assign its inputs — in one call."""
    applied = {}
    if name is not None:
        applied["label"] = await set_channel_label("mix", mix_num, name=name)
    if fader_db is not None:
        applied["fader"] = await set_fader_level("mix", mix_num, fader_db)
    if exclusive_inputs is not None:
        applied["inputs"] = await set_mix_exclusive_inputs(mix_num, exclusive_inputs, send_level_db)
    return {"ok": True, "mix": mix_num, "applied": applied}
```

- [ ] **Step 2: Test — append to `tests/unit/test_macros.py`**

```python
@pytest.mark.asyncio
async def test_label_channels_bulk(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.label_channels({"1": {"name": "Kick"}, "2": {"name": "Snare"}})
    assert result["ok"] is True
    assert mock_client.set.await_count == 2


@pytest.mark.asyncio
async def test_configure_mix_bus_combines_steps(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    srv._safety.mode = "limited"

    result = await srv.configure_mix_bus(
        1, name="FOH", fader_db=0.0, exclusive_inputs=[1], send_level_db=-6.0
    )
    assert result["ok"] is True
    # 1 label + 1 mix-fader + 16 on-writes + 1 level-write = 19
    assert mock_client.set.await_count == 19
```

- [ ] **Step 3: Run & commit**

```bash
.venv/bin/pytest tests/unit/test_macros.py -v
git add src/dm3_mcp/server.py tests/unit/test_macros.py
git commit -m "feat(mcp): macros — label_channels, apply_channel_preset, configure_mix_bus"
```

---

### Task 31: Macro — `ramp_fader`

**Files:**
- Modify: `src/dm3_mcp/server.py`

- [ ] **Step 1: Append**

```python
from .rcp.types import raw_to_db as _raw_to_db_fn


@mcp.tool()
async def ramp_fader(
    target_type: str,
    target_num: int,
    target_db: float,
    duration_ms: int = 500,
    steps: int = 20,
    override_safety: bool = False,
) -> dict:
    """Smoothly move a fader to target_db over duration_ms (linear in dB space)."""
    if target_type not in _FADER_LEVEL_ADDRESS:
        return {"ok": False, "error": {"code": "bad_target_type"}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    addr = _FADER_LEVEL_ADDRESS[target_type]
    # Read current
    current_entry = _cache.get(addr, target_num - 1, 0)
    if current_entry is None:
        return {"ok": False, "error": {"code": "no_cached_start", "message": "call get_channel_state first"}}
    start_db = _raw_to_db_fn(int(current_entry.value))
    if start_db == float("-inf"):
        start_db = -80.0  # treat -∞ as -80 dB for ramping (audibly silent, avoids log singularity)
    target_effective = max(target_db, -80.0) if target_db != float("-inf") else -80.0
    step_sleep = (duration_ms / 1000) / max(steps, 1)
    points = []
    for i in range(1, steps + 1):
        value_db = start_db + (target_effective - start_db) * (i / steps)
        try:
            _safety.check_fader_db(value_db, override=override_safety)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": {"code": "safety", "message": str(e), "completed_steps": points}}
        raw = db_to_raw(value_db)
        if _safety.should_send():
            await _client.set(addr, target_num - 1, 0, raw)
            _cache.record_set(addr, target_num - 1, 0, raw)
        points.append(round(value_db, 2))
        await asyncio.sleep(step_sleep)
    # Final step: if target was -inf, write NEG_INF_RAW explicitly
    if target_db == float("-inf") and _safety.should_send():
        await _client.set(addr, target_num - 1, 0, NEG_INF_RAW)
        _cache.record_set(addr, target_num - 1, 0, NEG_INF_RAW)
    return {"ok": True, "target_type": target_type, "target_num": target_num, "final_db": target_db, "points": points}
```

- [ ] **Step 2: Commit**

```bash
git add src/dm3_mcp/server.py
git commit -m "feat(mcp): macro — ramp_fader for smooth fade transitions"
```

---

### Task 32: Scene metadata store

**Files:**
- Create: `src/dm3_mcp/state/scenes.py`
- Create: `tests/unit/test_scenes_store.py`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_scenes_store.py
from pathlib import Path

from dm3_mcp.state.scenes import SceneStore


def test_roundtrip_store(tmp_path: Path):
    f = tmp_path / "scenes.json"
    s = SceneStore(f)
    s.upsert("A", 5, name="Worship band", description="Acoustic + bass", tags=["worship"])
    s.flush()

    s2 = SceneStore(f)
    s2.load()
    e = s2.get("A", 5)
    assert e is not None
    assert e["name"] == "Worship band"
    assert e["tags"] == ["worship"]


def test_list_filters_by_query(tmp_path: Path):
    f = tmp_path / "scenes.json"
    s = SceneStore(f)
    s.upsert("A", 1, name="Podcast solo")
    s.upsert("A", 2, name="Podcast duo")
    s.upsert("A", 3, name="Theater")
    s.flush()

    matches = s.list_(query="Podcast")
    assert len(matches) == 2


def test_atomic_write_does_not_corrupt(tmp_path: Path):
    f = tmp_path / "scenes.json"
    s = SceneStore(f)
    s.upsert("A", 1, name="One")
    s.flush()
    # Ensure we can load after write
    s2 = SceneStore(f)
    s2.load()
    assert s2.get("A", 1)["name"] == "One"
```

- [ ] **Step 2: Implementation**

```python
# src/dm3_mcp/state/scenes.py
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class SceneStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = {"version": 1, "console": {}, "scenes": {}}

    def load(self) -> None:
        if self.path.exists():
            self._data = json.loads(self.path.read_text())

    def upsert(
        self,
        bank: str,
        number: int,
        *,
        name: str,
        description: str = "",
        tags: list[str] | None = None,
        input_summary: dict[str, str] | None = None,
        notes: str = "",
    ) -> None:
        key = f"{bank}:{number}"
        now = datetime.now(timezone.utc).isoformat()
        existing = self._data["scenes"].get(key, {})
        self._data["scenes"][key] = {
            "bank": bank,
            "number": number,
            "name": name,
            "description": description,
            "tags": tags or [],
            "created_at": existing.get("created_at", now),
            "last_used_at": existing.get("last_used_at"),
            "use_count": existing.get("use_count", 0),
            "notes": notes,
            "input_summary": input_summary or {},
        }

    def mark_recalled(self, bank: str, number: int) -> None:
        key = f"{bank}:{number}"
        entry = self._data["scenes"].get(key)
        if entry is None:
            return
        entry["last_used_at"] = datetime.now(timezone.utc).isoformat()
        entry["use_count"] = entry.get("use_count", 0) + 1

    def get(self, bank: str, number: int) -> dict | None:
        return self._data["scenes"].get(f"{bank}:{number}")

    def list_(self, bank: str | None = None, query: str | None = None, tags: list[str] | None = None) -> list[dict]:
        items = list(self._data["scenes"].values())
        if bank:
            items = [s for s in items if s["bank"] == bank]
        if query:
            q = query.lower()
            items = [s for s in items if q in s["name"].lower() or q in (s.get("description") or "").lower()]
        if tags:
            items = [s for s in items if set(tags).issubset(set(s.get("tags") or []))]
        return items

    def flush(self) -> None:
        fd, tmp_path = tempfile.mkstemp(prefix="scenes-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp_path, self.path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
```

- [ ] **Step 3: Run & commit**

```bash
.venv/bin/pytest tests/unit/test_scenes_store.py -v
git add src/dm3_mcp/state/scenes.py tests/unit/test_scenes_store.py
git commit -m "feat(state): SceneStore with atomic JSON persistence + query/tags filter"
```

---

### Task 33: Scene tools — `list_scenes`, `recall_scene`, `store_current_as_scene`, `recall_scene_by_name`, `get_scene_metadata`

**Files:**
- Modify: `src/dm3_mcp/server.py`
- Create: `tests/unit/test_scene_tools.py`

- [ ] **Step 1: Append to `server.py`**

```python
from .state.scenes import SceneStore

_scene_store = SceneStore(_config.scenes_file)
try:
    _scene_store.load()
except Exception:
    log.exception("Failed to load scene metadata; starting empty")


def _bank_to_int(bank: str) -> int:
    if bank.upper() == "A":
        return 0
    if bank.upper() == "B":
        return 1
    raise ValueError(f"bank must be 'A' or 'B', got {bank!r}")


@mcp.tool()
async def list_scenes(
    bank: str | None = None,
    query: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """List scenes with local metadata, optionally filtered."""
    return {"ok": True, "scenes": _scene_store.list_(bank=bank, query=query, tags=tags)}


@mcp.tool()
async def recall_scene(bank: str, number: int) -> dict:
    """Recall a scene by (bank, number)."""
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    try:
        bank_int = _bank_to_int(bank)
    except ValueError as e:
        return {"ok": False, "error": {"code": "bad_bank", "message": str(e)}}
    try:
        await _client.recall_scene(bank_int, number)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": {"code": "rcp_error", "message": str(e)}}
    _scene_store.mark_recalled(bank.upper(), number)
    _scene_store.flush()
    meta = _scene_store.get(bank.upper(), number)
    return {"ok": True, "bank": bank.upper(), "number": number, "metadata": meta}


@mcp.tool()
async def recall_scene_by_name(name: str) -> dict:
    """Fuzzy-find a scene by name from local metadata and recall it."""
    matches = _scene_store.list_(query=name)
    if not matches:
        return {"ok": False, "error": {"code": "not_found", "message": name}}
    if len(matches) > 1:
        return {
            "ok": False,
            "error": {
                "code": "ambiguous",
                "candidates": [f"{m['bank']}:{m['number']} {m['name']}" for m in matches],
            },
        }
    m = matches[0]
    return await recall_scene(m["bank"], m["number"])


@mcp.tool()
async def store_current_as_scene(
    bank: str,
    number: int,
    name: str,
    description: str = "",
    tags: list[str] | None = None,
) -> dict:
    """Save the console's current state to a scene slot and record metadata."""
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    try:
        bank_int = _bank_to_int(bank)
    except ValueError as e:
        return {"ok": False, "error": {"code": "bad_bank", "message": str(e)}}
    try:
        await _client.store_scene(bank_int, number)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": {"code": "rcp_error", "message": str(e)}}
    input_summary = {}
    for ch in range(1, 17):
        entry = _cache.get("MIXER:Current/InCh/Label/Name", ch - 1, 0)
        if entry and entry.value:
            input_summary[str(ch)] = str(entry.value)
    _scene_store.upsert(
        bank.upper(), number,
        name=name, description=description, tags=tags or [],
        input_summary=input_summary,
    )
    _scene_store.flush()
    return {"ok": True, "bank": bank.upper(), "number": number, "name": name}


@mcp.tool()
async def get_scene_metadata(bank: str, number: int) -> dict:
    """Read local metadata for a scene slot."""
    meta = _scene_store.get(bank.upper(), number)
    if meta is None:
        return {"ok": False, "error": {"code": "not_found"}}
    return {"ok": True, "metadata": meta}
```

- [ ] **Step 2: Test**

```python
# tests/unit/test_scene_tools.py
from pathlib import Path
from unittest.mock import AsyncMock
import pytest

import dm3_mcp.server as srv
from dm3_mcp.state.scenes import SceneStore


@pytest.fixture
def fresh_store(tmp_path: Path, monkeypatch):
    store = SceneStore(tmp_path / "scenes.json")
    monkeypatch.setattr(srv, "_scene_store", store)
    return store


@pytest.mark.asyncio
async def test_store_and_recall_scene(monkeypatch, fresh_store):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)

    # pre-populate a label in the cache so input_summary fills
    srv._cache.record_set("MIXER:Current/InCh/Label/Name", 0, 0, "Kick")

    stored = await srv.store_current_as_scene("A", 5, "Test", description="")
    assert stored["ok"] is True

    recalled = await srv.recall_scene("A", 5)
    assert recalled["ok"] is True
    assert recalled["metadata"]["name"] == "Test"


@pytest.mark.asyncio
async def test_recall_by_name_ambiguous(monkeypatch, fresh_store):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    fresh_store.upsert("A", 1, name="Podcast solo")
    fresh_store.upsert("A", 2, name="Podcast duo")
    result = await srv.recall_scene_by_name("Podcast")
    assert result["ok"] is False
    assert result["error"]["code"] == "ambiguous"


@pytest.mark.asyncio
async def test_list_scenes_filter(monkeypatch, fresh_store):
    fresh_store.upsert("A", 1, name="Worship")
    fresh_store.upsert("B", 1, name="Theater")
    result = await srv.list_scenes(bank="A")
    assert result["ok"] is True
    assert len(result["scenes"]) == 1
    assert result["scenes"][0]["name"] == "Worship"
```

- [ ] **Step 3: Run & commit**

```bash
.venv/bin/pytest tests/unit/test_scene_tools.py -v
git add src/dm3_mcp/server.py tests/unit/test_scene_tools.py
git commit -m "feat(mcp): scene tools — list, recall, recall-by-name, store, metadata"
```

---

### Task 34: `run_probe` MCP tool

**Files:**
- Modify: `src/dm3_mcp/server.py`

- [ ] **Step 1: Append**

```python
import subprocess
import sys


@mcp.tool()
async def run_probe(host: str | None = None) -> dict:
    """Run scripts/probe.py against the DM3 and return the results path."""
    effective_host = host or _config.host
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "scripts/probe.py",
        "--host",
        effective_host,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {
        "ok": proc.returncode == 0,
        "stdout": stdout.decode(errors="replace"),
        "stderr": stderr.decode(errors="replace"),
        "exit_code": proc.returncode,
    }
```

- [ ] **Step 2: Commit**

```bash
git add src/dm3_mcp/server.py
git commit -m "feat(mcp): expose run_probe tool for on-demand discovery"
```

---

## Phase 6 (M5) — Release prep

### Task 35: Full test sweep

- [ ] **Step 1: Run all unit tests**

```bash
.venv/bin/pytest tests/unit -v
```

Expected: all green.

- [ ] **Step 2: Lint**

```bash
.venv/bin/ruff check src/ tests/ scripts/
.venv/bin/ruff format --check src/ tests/ scripts/
```

Fix any issues inline and commit.

---

### Task 36: 🎯 LIVE HARDWARE — end-to-end smoke

- [ ] **Step 1: Run live integration suite**

```bash
DM3_HOST=192.168.0.128 .venv/bin/pytest tests/integration -m live_hardware -v
```

- [ ] **Step 2: Manual scenario walk-through**

Launch the server standalone:

```bash
.venv/bin/dm3-mcp
```

From a separate Python REPL, exercise the tools:

```python
import asyncio
from dm3_mcp import server

async def demo():
    print(await server.connect_console())
    print(await server.label_channels({"1":{"name":"Kick"},"2":{"name":"Snare"}}))
    print(await server.set_mix_exclusive_inputs(1, [1,2], level_db=-6.0))
    print(await server.set_fader_level("inch", 1, -10.0))
    print(await server.get_all_labels())
    print(await server.store_current_as_scene("A", 99, "Smoke Test", tags=["smoke"]))
    print(await server.recall_scene_by_name("Smoke Test"))
    print(await server.disconnect_console())

asyncio.run(demo())
```

Expected: every call returns `ok: True`; fader 1 moves, labels appear on surface, scene 99 stores and recalls without errors.

- [ ] **Step 3: Commit evidence**

If anything diverged from expectations, update `docs/superpowers/specs/M0-probe-findings.md` with the discovery and commit.

---

### Task 37: README + MCP registration guide

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the README**

Replace contents with a complete setup + usage guide (see handoff scaffolding for structure).

- [ ] **Step 2: Tag release**

```bash
git tag v0.1.0
git log --oneline | head
```

- [ ] **Step 3: Final commit**

```bash
git add README.md
git commit -m "docs: v0.1.0 release notes and usage guide"
```

---

## Self-review checklist (run before handoff)

- [ ] `pytest tests/unit -v` — all green with no skips.
- [ ] `ruff check` and `ruff format --check` — clean.
- [ ] Every tool listed in the spec has a corresponding task. (Count: 33.)
- [ ] No steps contain `TBD`, `TODO`, or `# implement later`.
- [ ] Live-hardware tasks are clearly marked and skippable.
- [ ] `git log` shows one commit per task.
- [ ] M0 probe findings are captured in `docs/superpowers/specs/M0-probe-findings.md`.

---

## Handoff prerequisites (target machine)

Before the target machine's Claude Code can execute this plan, confirm:

1. The repo has been synced (git pull or file transfer, see top-level `README.md`).
2. Python 3.11+ and `uv` installed.
3. DM3 reachable: `ping <DM3_IP>` succeeds.
4. Environment variable `DM3_HOST` set to the DM3's IP in the shell where tests run.
5. The DM3 has "For Mixer Control" enabled with a static IP.
6. The DM3 scene slot B99 is safe to overwrite (the probe uses it as a scratch slot).
