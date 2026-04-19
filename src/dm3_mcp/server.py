"""FastMCP server entrypoint for the Yamaha DM3 MCP."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

from mcp.server.fastmcp import FastMCP

from .config import Config
from .rcp.client import RcpClient
from .rcp.codec import encode_sscurrent
from .rcp.types import NEG_INF_RAW, db_to_raw
from .state.cache import StateCache
from .state.initial_sync import run_initial_sync
from .state.scenes import SceneStore
from .state.wiring import wire_cache_to_client
from .tools.safety import SafetyContext

log = logging.getLogger(__name__)

mcp = FastMCP("yamaha-dm3")
_config = Config()
_client: RcpClient | None = None
_cache = StateCache()
_safety = SafetyContext(max_fader_db=_config.max_fader_db)
_scene_store = SceneStore(_config.scenes_file)
try:
    _scene_store.load()
except Exception:  # noqa: BLE001
    log.exception("failed to load scene metadata; starting empty")


# ---------------------------------------------------------------------------
# Address tables
# ---------------------------------------------------------------------------

_LABEL_NAME_ADDRESS = {
    "inch": "MIXER:Current/InCh/Label/Name",
    "stinch": "MIXER:Current/StInCh/Label/Name",
    "fxrtn": "MIXER:Current/FxRtnCh/Label/Name",
    "mix": "MIXER:Current/Mix/Label/Name",
    "matrix": "MIXER:Current/Mtrx/Label/Name",
    "stereo": "MIXER:Current/St/Label/Name",
    "fx": "MIXER:Current/Fx/Label/Name",
}

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

_LINK_GROUP_ADDRESS = {
    "inch": "MIXER:Current/InputChLink/InCh/Assign",
    "stinch": "MIXER:Current/InputChLink/StInCh/Assign",
    "fxrtn": "MIXER:Current/InputChLink/FxRtnCh/Assign",
}


def _bank_to_letter(bank: str) -> str:
    bank_upper = bank.upper()
    if bank_upper not in ("A", "B"):
        raise ValueError(f"bank must be 'A' or 'B', got {bank!r}")
    return bank_upper


# ---------------------------------------------------------------------------
# Connection / status / safety
# ---------------------------------------------------------------------------


@mcp.tool()
async def connect_console(host: str | None = None, port: int = 49280) -> dict:
    """Open a persistent TCP connection to the DM3 and prime the state cache."""
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
    """Set the global safety mode: 'preview' | 'live' | 'limited'."""
    if mode not in ("preview", "live", "limited"):
        return {"ok": False, "error": {"code": "bad_mode", "message": f"unknown mode {mode!r}"}}
    _safety.mode = mode  # type: ignore[assignment]
    return {"ok": True, "mode": mode}


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_channel_state(ch_num: int) -> dict:
    """Return cached state for an input channel (1-16)."""
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
    """Return cached state for a mix bus (1-6)."""
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
    """Ask the console for the current scene per bank.

    M0: `sscurrent_ex scene_<bank>` returns InvalidArgument until the first
    recall in this session, so we treat that as 'unknown' rather than an error.
    """
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    out: dict = {"ok": True}
    for bank in ("A", "B"):
        try:
            resp = await _client._send(encode_sscurrent(bank), timeout=2.0)
            out[bank] = resp.raw
        except Exception as e:  # noqa: BLE001
            out[bank] = f"unknown ({e})"
    return out


@mcp.tool()
async def read_meter(target: str, num: int, pickoff: str = "PostOn") -> dict:
    """Read a live meter value. Bypasses the cache."""
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
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


# ---------------------------------------------------------------------------
# Write primitives
# ---------------------------------------------------------------------------


@mcp.tool()
async def set_channel_label(
    ch_type: str,
    ch_num: int,
    name: str | None = None,
    color: str | None = None,
    icon: str | None = None,
    category: str | None = None,
) -> dict:
    """Set any subset of label fields for a channel/bus."""
    if ch_type not in _LABEL_NAME_ADDRESS:
        return {"ok": False, "error": {"code": "bad_ch_type", "message": ch_type}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    applied: dict[str, str] = {}
    for field_name, value in (("Name", name), ("Color", color), ("Icon", icon), ("Category", category)):
        if value is None:
            continue
        base = _LABEL_NAME_ADDRESS[ch_type].rsplit("/", 1)[0]
        address = f"{base}/{field_name}"
        if _safety.should_send():
            await _client.set(address, ch_num - 1, 0, value)
            _cache.record_set(address, ch_num - 1, 0, value)
        applied[field_name.lower()] = value
    return {"ok": True, "ch_type": ch_type, "ch_num": ch_num, "applied": applied}


@mcp.tool()
async def set_fader_level(
    target_type: str,
    target_num: int,
    level_db: float,
    override_safety: bool = False,
) -> dict:
    """Set a fader level in dB. Pass float('-inf') for -inf."""
    if target_type not in _FADER_LEVEL_ADDRESS:
        return {"ok": False, "error": {"code": "bad_target_type", "message": target_type}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    try:
        _safety.check_fader_db(
            level_db if level_db != float("-inf") else -999, override=override_safety
        )
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


@mcp.tool()
async def set_mute_group(group_num: int, active: bool) -> dict:
    """Activate or release a mute group (1-6). Active=True means the group is muting."""
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
    """Set analog head-amp gain on an input channel (0-64 dB, integer)."""
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


@mcp.tool()
async def set_send(
    from_type: str,
    from_num: int,
    to_type: str,
    to_num: int,
    level_db: float | None = None,
    on: bool | None = None,
    pan: int | None = None,
    prepost: str | None = None,
) -> dict:
    """Update a send atomically. Any subset of level/on/pan/prepost in one call."""
    key = (from_type, to_type)
    if key not in _SEND_BASE:
        return {"ok": False, "error": {"code": "bad_route", "message": f"{from_type}->{to_type}"}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    base = _SEND_BASE[key]
    x = from_num - 1
    y = 0 if to_type == "stereo" else to_num - 1
    applied: dict = {}

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
    applied: dict = {"on": on}
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
    """Configure a PEQ band (1-4). Any subset of freq/gain/q/type updates."""
    if ch_type not in _PEQ_BASE:
        return {"ok": False, "error": {"code": "bad_ch_type"}}
    if not 1 <= band <= 4:
        return {"ok": False, "error": {"code": "bad_band"}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    base = _PEQ_BASE[ch_type]
    applied: dict = {}

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


@mcp.tool()
async def set_channel_link_group(input_type: str, ch_num: int, group: int) -> dict:
    """Assign a channel to an input-link group. group=0 NONE, 1-9 = A-I."""
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


@mcp.tool()
async def emergency_mute_all(on: bool = True) -> dict:
    """Mute (on=True) or unmute every input channel immediately. Bypasses preview safety."""
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    raw = 0 if on else 1  # Yamaha 'on' = audible; mute -> write 0
    address = "MIXER:Current/InCh/Fader/On"
    muted: list[int] = []
    for x in range(16):
        await _client.set(address, x, 0, raw)
        _cache.record_set(address, x, 0, raw)
        muted.append(x + 1)
    for addr, count in (
        ("MIXER:Current/StInCh/Fader/On", 2),
        ("MIXER:Current/FxRtnCh/Fader/On", 4),
    ):
        for x in range(count):
            await _client.set(addr, x, 0, raw)
            _cache.record_set(addr, x, 0, raw)
    return {"ok": True, "muted_on": on, "inch_count": len(muted)}


# ---------------------------------------------------------------------------
# Macros (M4)
# ---------------------------------------------------------------------------


@mcp.tool()
async def set_mix_exclusive_inputs(
    mix_num: int,
    input_channels: list[int],
    level_db: float = 0.0,
) -> dict:
    """Turn ALL input sends to this mix off, except the listed ones (which are turned on)."""
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


@mcp.tool()
async def label_channels(mapping: dict) -> dict:
    """Bulk-label inputs. Keys are channel numbers (str-coerced); values are dicts."""
    results: dict = {}
    for k, v in mapping.items():
        ch_num = int(k)
        r = await set_channel_label("inch", ch_num, **v)
        results[ch_num] = r
    return {"ok": True, "results": results}


@mcp.tool()
async def apply_channel_preset(ch_num: int, preset: dict) -> dict:
    """Apply a whole-channel configuration in one call."""
    steps = []
    if any(k in preset for k in ("name", "color", "icon", "category")):
        steps.append(
            await set_channel_label(
                "inch",
                ch_num,
                name=preset.get("name"),
                color=preset.get("color"),
                icon=preset.get("icon"),
                category=preset.get("category"),
            )
        )
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
    """Name a mix, set its fader, and exclusively assign its inputs in one call."""
    applied: dict = {}
    if name is not None:
        applied["label"] = await set_channel_label("mix", mix_num, name=name)
    if fader_db is not None:
        applied["fader"] = await set_fader_level("mix", mix_num, fader_db)
    if exclusive_inputs is not None:
        applied["inputs"] = await set_mix_exclusive_inputs(mix_num, exclusive_inputs, send_level_db)
    return {"ok": True, "mix": mix_num, "applied": applied}


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
    current_entry = _cache.get(addr, target_num - 1, 0)
    if current_entry is None:
        return {"ok": False, "error": {"code": "no_cached_start", "message": "call get_channel_state first"}}
    start_db = float(current_entry.value) / 100
    if start_db <= -327.0:  # neg-inf raw was -32768 / 100
        start_db = -80.0
    target_effective = max(target_db, -80.0) if target_db != float("-inf") else -80.0
    step_sleep = (duration_ms / 1000) / max(steps, 1)
    points: list[float] = []
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
    if target_db == float("-inf") and _safety.should_send():
        await _client.set(addr, target_num - 1, 0, NEG_INF_RAW)
        _cache.record_set(addr, target_num - 1, 0, NEG_INF_RAW)
    return {
        "ok": True,
        "target_type": target_type,
        "target_num": target_num,
        "final_db": target_db,
        "points": points,
    }


# ---------------------------------------------------------------------------
# Scene tools (M4) — store is metadata-only per M0 findings
# ---------------------------------------------------------------------------


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
        bank_letter = _bank_to_letter(bank)
    except ValueError as e:
        return {"ok": False, "error": {"code": "bad_bank", "message": str(e)}}
    try:
        await _client.recall_scene(bank_letter, number)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": {"code": "rcp_error", "message": str(e)}}
    _scene_store.mark_recalled(bank_letter, number)
    _scene_store.flush()
    meta = _scene_store.get(bank_letter, number)
    return {"ok": True, "bank": bank_letter, "number": number, "metadata": meta}


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
    """Record metadata for an existing scene slot (M0: console store is not exposed via RCP).

    The actual scene contents must be stored on the console panel; this tool only
    captures local metadata + an input_summary snapshot from the cache.
    """
    try:
        bank_letter = _bank_to_letter(bank)
    except ValueError as e:
        return {"ok": False, "error": {"code": "bad_bank", "message": str(e)}}
    input_summary: dict[str, str] = {}
    for ch in range(1, 17):
        entry = _cache.get("MIXER:Current/InCh/Label/Name", ch - 1, 0)
        if entry and entry.value:
            input_summary[str(ch)] = str(entry.value)
    _scene_store.upsert(
        bank_letter,
        number,
        name=name,
        description=description,
        tags=tags or [],
        input_summary=input_summary,
    )
    _scene_store.flush()
    return {
        "ok": True,
        "bank": bank_letter,
        "number": number,
        "name": name,
        "console_store": False,
        "note": "DM3 does not expose scene store via RCP; press STORE on the console first.",
    }


@mcp.tool()
async def get_scene_metadata(bank: str, number: int) -> dict:
    """Read local metadata for a scene slot."""
    try:
        bank_letter = _bank_to_letter(bank)
    except ValueError as e:
        return {"ok": False, "error": {"code": "bad_bank", "message": str(e)}}
    meta = _scene_store.get(bank_letter, number)
    if meta is None:
        return {"ok": False, "error": {"code": "not_found"}}
    return {"ok": True, "metadata": meta}


# ---------------------------------------------------------------------------
# Cue & Monitor (v0.2)
# ---------------------------------------------------------------------------

# Cue On addresses by source type. Stereo bus has 2 indices (L/R), Fx has 2 units.
_CUE_ON_ADDRESS = {
    "inch": ("MIXER:Current/Cue/InCh/On", 16),
    "stinch": ("MIXER:Current/Cue/StInCh/On", 2),
    "fxrtn": ("MIXER:Current/Cue/FxRtnCh/On", 4),
    "mix": ("MIXER:Current/Cue/Mix/On", 6),
    "matrix": ("MIXER:Current/Cue/Mtrx/On", 2),
    "stereo": ("MIXER:Current/Cue/St/On", 2),
    "fx": ("MIXER:Current/Cue/Fx/On", 2),
}

_MONITOR_SOURCE_ADDRESS = {
    "mix": ("MIXER:Current/Monitor/St/SourceCh/Mix", 6),
    "matrix": ("MIXER:Current/Monitor/St/SourceCh/Mtrx", 2),
    "stereo": ("MIXER:Current/Monitor/St/SourceCh/St", 2),
    "rec": ("MIXER:Current/Monitor/St/SourceCh/Rec", 1),
    "usb": ("MIXER:Current/Monitor/St/SourceCh/USB", 1),
}


@mcp.tool()
async def set_cue(
    target_type: str,
    target_num: int,
    on: bool = True,
    exclusive: bool = False,
) -> dict:
    """Toggle cue (PFL/AFL listen) on a channel/bus.

    Args:
        target_type: 'inch'|'stinch'|'fxrtn'|'mix'|'matrix'|'stereo'|'fx'
        target_num: 1-based index within the target type.
        on: True to enable cue, False to clear.
        exclusive: when True (and on=True), turns off ALL other cues first
            so only this source is being monitored.
    """
    if target_type not in _CUE_ON_ADDRESS:
        return {"ok": False, "error": {"code": "bad_target_type", "message": target_type}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    address, max_count = _CUE_ON_ADDRESS[target_type]
    if not 1 <= target_num <= max_count:
        return {
            "ok": False,
            "error": {"code": "bad_target_num", "message": f"1..{max_count}"},
        }

    cleared = 0
    if exclusive and on:
        # Turn off every cue across every category
        for ttype, (addr, count) in _CUE_ON_ADDRESS.items():
            for x in range(count):
                if ttype == target_type and x == target_num - 1:
                    continue
                if _safety.should_send():
                    await _client.set(addr, x, 0, 0)
                    _cache.record_set(addr, x, 0, 0)
                cleared += 1

    raw = 1 if on else 0
    if _safety.should_send():
        await _client.set(address, target_num - 1, 0, raw)
        _cache.record_set(address, target_num - 1, 0, raw)
    return {
        "ok": True,
        "target_type": target_type,
        "target_num": target_num,
        "on": on,
        "exclusive_cleared": cleared,
    }


@mcp.tool()
async def clear_all_cues() -> dict:
    """Turn off cue across every category (panic-clear the solo bus)."""
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    cleared = 0
    for addr, count in _CUE_ON_ADDRESS.values():
        for x in range(count):
            if _safety.should_send():
                await _client.set(addr, x, 0, 0)
                _cache.record_set(addr, x, 0, 0)
            cleared += 1
    return {"ok": True, "cleared_count": cleared}


@mcp.tool()
async def get_active_cue() -> dict:
    """Read the read-only `MIXER:Current/Cue/ActiveCue` (currently auditioned source)."""
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    try:
        result = await _client.get("MIXER:Current/Cue/ActiveCue", 0, 0)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": {"code": "rcp_error", "message": str(e)}}
    return {"ok": True, "active_cue": result.value}


@mcp.tool()
async def set_cue_mode(
    mode: str | None = None,
    in_point: str | None = None,
    out_point: str | None = None,
) -> dict:
    """Configure global cue routing.

    Args:
        mode: cue mode string (e.g. 'MIX', 'MIX+CUE'); range 0..5 per the param dump.
        in_point: input-channel cue tap point (e.g. 'PFL', 'AFL', etc.).
        out_point: output-channel cue tap point.
    """
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    applied: dict = {}
    for value, address in (
        (mode, "MIXER:Current/Cue/CueMode"),
        (in_point, "MIXER:Current/Cue/InCh/Point"),
        (out_point, "MIXER:Current/Cue/OutCh/Point"),
    ):
        if value is None:
            continue
        if _safety.should_send():
            await _client.set(address, 0, 0, value)
            _cache.record_set(address, 0, 0, value)
        applied[address.rsplit("/", 1)[-1]] = value
    return {"ok": True, "applied": applied}


@mcp.tool()
async def set_monitor(
    on: bool | None = None,
    level_db: float | None = None,
    mono: bool | None = None,
    cue_interrupts: bool | None = None,
    override_safety: bool = False,
) -> dict:
    """Atomic monitor-master control.

    Args:
        on: enable/disable the monitor bus.
        level_db: monitor fader level in dB (clamped per safety mode).
        mono: collapse stereo monitor to mono.
        cue_interrupts: when True, an active cue replaces the monitor source.
    """
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    applied: dict = {}

    async def _apply(addr: str, value, label: str):
        if _safety.should_send():
            await _client.set(addr, 0, 0, value)
            _cache.record_set(addr, 0, 0, value)
        applied[label] = value

    if on is not None:
        await _apply("MIXER:Current/Monitor/On", 1 if on else 0, "on")
    if level_db is not None:
        try:
            _safety.check_fader_db(
                level_db if level_db != float("-inf") else -999, override=override_safety
            )
            raw = db_to_raw(level_db) if level_db != float("-inf") else NEG_INF_RAW
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": {"code": "safety", "message": str(e)}}
        await _apply("MIXER:Current/Monitor/Fader/Level", raw, "level_db")
    if mono is not None:
        await _apply("MIXER:Current/Monitor/St/MonoMonitor", 1 if mono else 0, "mono")
    if cue_interrupts is not None:
        await _apply(
            "MIXER:Current/Monitor/CueInterruption", 1 if cue_interrupts else 0, "cue_interrupts"
        )
    return {"ok": True, "applied": applied}


@mcp.tool()
async def set_monitor_source(
    source_type: str,
    source_num: int = 1,
    on: bool = True,
    exclusive: bool = False,
) -> dict:
    """Pick which bus(es) feed the monitor output.

    Args:
        source_type: 'mix' (1-6), 'matrix' (1-2), 'stereo' (1-2), 'rec' (1), 'usb' (1).
        source_num: 1-based index within that type.
        on: True to enable, False to disable.
        exclusive: when True and on=True, turns off all other monitor sources first.
    """
    if source_type not in _MONITOR_SOURCE_ADDRESS:
        return {"ok": False, "error": {"code": "bad_source_type", "message": source_type}}
    if _client is None:
        return {"ok": False, "error": {"code": "not_connected"}}
    address, max_count = _MONITOR_SOURCE_ADDRESS[source_type]
    if not 1 <= source_num <= max_count:
        return {
            "ok": False,
            "error": {"code": "bad_source_num", "message": f"1..{max_count}"},
        }
    cleared = 0
    if exclusive and on:
        for stype, (addr, count) in _MONITOR_SOURCE_ADDRESS.items():
            for x in range(count):
                if stype == source_type and x == source_num - 1:
                    continue
                if _safety.should_send():
                    await _client.set(addr, x, 0, 0)
                    _cache.record_set(addr, x, 0, 0)
                cleared += 1
    raw = 1 if on else 0
    if _safety.should_send():
        await _client.set(address, source_num - 1, 0, raw)
        _cache.record_set(address, source_num - 1, 0, raw)
    return {
        "ok": True,
        "source_type": source_type,
        "source_num": source_num,
        "on": on,
        "exclusive_cleared": cleared,
    }


# ---------------------------------------------------------------------------
# Development helper
# ---------------------------------------------------------------------------


@mcp.tool()
async def run_probe(host: str | None = None) -> dict:
    """Run scripts/probe.py against the DM3 and return the results."""
    effective_host = host or _config.host
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "scripts/probe.py",
        "--host",
        effective_host,
        "--yes",
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


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run()


if __name__ == "__main__":
    main()
