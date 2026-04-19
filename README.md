# Yamaha DM3 MCP

An MCP server that lets Claude set up a Yamaha DM3 digital mixer via natural-language prompts: labeling, gain, sends, mute groups, scenes.

## Status

**v0.2.0 — implementation + cue/monitor coverage validated against a live DM3 (V3.00).**

73 unit tests pass; live integration tests pass against a real console. See [docs/superpowers/specs/M0-probe-findings.md](docs/superpowers/specs/M0-probe-findings.md) for the empirical protocol confirmation, including the dynamics dead-end (DM3 firmware does not expose comp/gate over RCP).

## What it does

A Python/FastMCP server that exposes **39 tools** to Claude for controlling a Yamaha DM3 over the RCP protocol on TCP port 49280. Tools group into:

- **Connection / safety** (4) — `connect_console`, `disconnect_console`, `get_connection_status`, `set_safety_mode`
- **Read tools** (6) — cache-backed channel/mix/label/mute-group/scene state, plus `read_meter`
- **Write primitives** (11) — labels, faders, channel on, mute groups, head-amp gain, phantom, sends, HPF, PEQ, channel link, emergency mute
- **Macros** (5) — `set_mix_exclusive_inputs`, `label_channels`, `apply_channel_preset`, `configure_mix_bus`, `ramp_fader`
- **Cue / Monitor** (6) — `set_cue`, `clear_all_cues`, `get_active_cue`, `set_cue_mode`, `set_monitor`, `set_monitor_source`
- **Scenes** (5) — `list_scenes`, `recall_scene`, `recall_scene_by_name`, `store_current_as_scene` (metadata only — see below), `get_scene_metadata`
- **Dev** (1) — `run_probe`

### Important M0 findings folded into v0.1

- **Scene STORE is not exposed over RCP.** Storing a scene must be done from the console panel; the `store_current_as_scene` tool only records local metadata against an existing slot. Recall works fine.
- **Scene recall uses string banks** (`scene_a`/`scene_b`), not integer banks.
- **Direct input patching is not exposed.** Use scene recall to swap input routing.
- **NOTIFYs broadcast across connections** — surface changes (and other clients' writes) flow into the cache automatically; no `subscribe` command exists.
- **OK responses include a trailing display string** (e.g. `OK set ... -1000 "-10.00"`); the codec ignores it for state purposes.

## Prerequisites

- Python 3.11+
- `uv` (`pip install uv` or https://docs.astral.sh/uv/getting-started/installation/)
- DM3 reachable on the LAN with `SETUP → NETWORK → For Mixer Control → Static IP` enabled
- `DM3_HOST` env var set to the console's IP (e.g. `192.168.10.130`)

## Install

```bash
uv venv
VIRTUAL_ENV="$PWD/.venv" uv pip install --python .venv/Scripts/python.exe -e ".[dev]"  # Windows
# or on POSIX:
# uv pip install -e ".[dev]"
```

## Run the unit tests

```bash
.venv/Scripts/python.exe -m pytest tests/unit -v
```

## Run the live integration tests (requires DM3)

```bash
DM3_HOST=192.168.10.130 .venv/Scripts/python.exe -m pytest tests/integration -m live_hardware -v
```

## End-to-end smoke

```bash
DM3_HOST=192.168.10.130 .venv/Scripts/python.exe scripts/smoke_demo.py
```

This will: connect, label channels 1 and 2, exclusively assign them to Mix 1 at -6 dB, set ch 1 fader to -10 dB, list all labels, save scene metadata, recall scene A:1, disconnect.

## Run the empirical probe

If the console firmware changes or you want to re-validate protocol behavior:

```bash
DM3_HOST=192.168.10.130 .venv/Scripts/python.exe scripts/probe.py --host $DM3_HOST --yes
```

Writes a timestamped JSON to `data/probe-results-*.json` (gitignored).

## Connect Claude Code to the server

```bash
claude mcp add yamaha-dm3 -- .venv/Scripts/python.exe -m dm3_mcp.server
```

Then in any Claude Code session:

- "Connect to the DM3 and tell me what's currently labeled."
- "Set mix 1 to inputs 1 and 2 only at 0 dB."
- "Label channels 1-4 as Kick, Snare, Hi-Hat, Overhead; set phantom on 3 and 4."
- "Recall the worship band scene."

## Safety

The server starts in `limited` safety mode with `max_fader_db = +6.0`. To override per-call: `set_fader_level(..., override_safety=True)`. To switch globally: `set_safety_mode("live")` (no clamp) or `set_safety_mode("preview")` (logs only, sends nothing). `emergency_mute_all(True)` is always reachable.

## Repository layout

```
.
├── CLAUDE.md                                          ← project orientation for Claude Code
├── README.md                                          ← this file
├── pyproject.toml                                     ← uv/hatchling build
├── data/
│   └── DM3 Parameters-2.txt                           ← vendored RCP parameter dump
├── DM3_osc_extracted.txt                              ← extracted Yamaha OSC spec text (cross-reference)
├── docs/superpowers/
│   ├── specs/2026-04-19-dm3-mcp-design.md             ← design spec
│   ├── specs/M0-probe-findings.md                     ← empirical findings
│   └── plans/2026-04-19-dm3-mcp-implementation.md     ← implementation plan
├── src/dm3_mcp/
│   ├── server.py                                      ← FastMCP server, 33 tools
│   ├── config.py
│   ├── rcp/{client.py, codec.py, params.py, types.py}
│   ├── state/{cache.py, views.py, wiring.py, initial_sync.py, scenes.py}
│   └── tools/safety.py
├── tests/
│   ├── unit/                                          ← 64 tests
│   └── integration/                                   ← live_hardware-marked
└── scripts/
    ├── probe.py                                       ← empirical RCP discovery
    ├── probe_scenes.py, probe_scenes2.py, probe_final.py  ← M0 follow-ups
    └── smoke_demo.py                                  ← live end-to-end
```

## License

MIT — see [LICENSE](LICENSE).
