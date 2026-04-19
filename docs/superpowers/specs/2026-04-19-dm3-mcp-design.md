# Yamaha DM3 MCP — Design Spec

**Date:** 2026-04-19
**Status:** Draft (pre-implementation)
**Target console:** Yamaha DM3 / DM3 Standard (16-ch digital mixer)

## Context

The Yamaha DM3 is a compact digital mixer used in live production. Setup work — patching, labeling, gain staging, routing mixes, building scenes — is repetitive and eats rehearsal time. An MCP server would let an operator describe setup in natural language ("set mix 1 to inputs 1 and 2 only, label channels 1-4 as drums, recall the worship scene") and have Claude apply it over the network.

The DM3 is reachable on the local LAN and exposes two relevant protocols:

- **RCP (Yamaha Remote Control Protocol)** — TCP port 49280, newline-delimited text. Proprietary but well-reverse-engineered via the Bitfocus Companion module.
- **OSC** — UDP port 49900. Officially documented by Yamaha, but covers only 145 parameters and omits PEQ, HPF/LPF, dynamics, meters, scene store.

We choose **RCP**: it has broader coverage, emits `NOTIFY` messages for surface changes (enabling state sync with zero polling), and TCP's delivery guarantees matter when mutating a live console.

## Goals

1. Let Claude drive common DM3 setup tasks via natural language: labeling, gain, mutes, sends, scene recall/store, mute groups.
2. Stay in sync with surface changes (engineer moves a fader, MCP knows).
3. Ship safely — no unexpected level jumps, no blind-firing into read-only addresses.
4. Be debuggable — single runtime, REPL-friendly, probe scripts share code with the MCP server.

## Non-goals

- Per-session Dante source routing (handled by Yamaha Dante Controller, outside our scope).
- Real-time mixing assistance during a show.
- Multi-console support (DM7, TF, CL/QL). The architecture leaves room for this but v1 targets DM3 only.
- Automated feedback detection / speaker protection beyond a fader-dB clamp.

## Protocol reference

### RCP command shapes

```
set <Address> <X> <Y> <Value>\n           e.g. set MIXER:Current/InCh/Fader/Level 0 0 -1000
get <Address> <X> <Y>\n                   e.g. get MIXER:Current/InCh/Fader/Level 0 0
ssrecall_ex <bank> <scene>\n              e.g. ssrecall_ex 0 5  (bank A, scene 5; bank encoding 0=A, 1=B — confirmed in M0)
```

### Response types

- `OK set ...` — acknowledged
- `OKm set ...` — acknowledged with clamped/modified value
- `OK get ... <value>` — response to a query
- `NOTIFY set ...` — unsolicited; fires when the console surface changes
- `ERROR <reason>` — failure

### Value encoding

- **dB** → integer × 100 (range -13800 to +1000; `-32768` = -∞)
- **Pan** → integer -63 (L63) to +63 (R63)
- **Bool** → 0 / 1
- **String** → quoted `"..."` when containing spaces or special chars; unquoted otherwise

### DM3 capacity (from parameter dump)

- 16 mono inputs, 2 stereo inputs, 4 FX returns
- 6 Mix buses, 2 Matrix buses, 1 Stereo master, 2 FX units
- 6 Mute groups, 9 Input Channel Link groups (A–I)
- Scenes: 100 per bank, banks A & B
- No DCA groups on DM3 (confirmed: `DCA Group: 0` in OSC spec)

### Patching constraint

Input patching (Dante source → console channel) is **not exposed** for DM3 in the `prminfo` dump. CL/QL exposes `MIXER:Current/InCh/Patch` as read-only; only Rivage PM exposes it as read-write. DM3 is in the same compact-console tier and almost certainly matches CL/QL behavior. **Verified empirically during Milestone 0.** Scene recall is the primary patching strategy.

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                      MCP Server (FastMCP)                      │
│                                                                 │
│   ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  │
│   │   Primitive    │  │     Macro      │  │     Scene      │  │
│   │     tools      │  │     tools      │  │   metadata     │  │
│   └───────┬────────┘  └───────┬────────┘  └───────┬────────┘  │
│           │                   │                   │            │
│           └───────────┬───────┴───────┬───────────┘            │
│                       ▼               ▼                        │
│            ┌──────────────────┐  ┌──────────────────┐         │
│            │   State cache    │  │  Scene metadata  │         │
│            │  (in-memory)     │  │  (JSON on disk)  │         │
│            └────────┬─────────┘  └──────────────────┘         │
│                     ▲                                          │
│                     │ NOTIFY events                            │
│            ┌────────┴─────────────────────┐                   │
│            │   RCP Client (asyncio)        │                   │
│            │   - TCP persistent socket     │                   │
│            │   - Command queue (5ms gap)   │                   │
│            │   - NOTIFY parser             │                   │
│            │   - Keepalive + reconnect     │                   │
│            └────────┬─────────────────────┘                   │
└─────────────────────┼──────────────────────────────────────────┘
                      │ TCP 49280
                      ▼
                ┌─────────────┐
                │  Yamaha DM3 │
                └─────────────┘
```

### Module boundaries

- **RCP client** has no MCP dependencies — reusable from probe scripts, tests, REPL.
- **State cache** owns the in-memory mirror; subscribes to NOTIFY.
- **MCP tools** are thin — delegate to primitives/macros/scenes.

## Repo layout

```
yamaha-dm3-mcp/
├── pyproject.toml
├── README.md
├── docs/superpowers/specs/2026-04-19-dm3-mcp-design.md
├── src/dm3_mcp/
│   ├── __init__.py
│   ├── server.py              # FastMCP entrypoint, tool registrations
│   ├── config.py              # host, port, safety limits, scene file path
│   ├── rcp/
│   │   ├── client.py          # async TCP client, queue, keepalive
│   │   ├── codec.py           # encode/decode set/get/notify/error
│   │   ├── params.py          # parsed DM3 Parameters-2.txt → schema
│   │   └── types.py           # dB conversion, pan conversion, enums
│   ├── state/
│   │   ├── cache.py           # in-memory console mirror
│   │   └── scenes.py          # scene metadata JSON store
│   └── tools/
│       ├── primitives.py      # 1:1 RCP wrappers
│       ├── macros.py          # batched intent tools
│       ├── scenes.py          # scene recall/store/list/find
│       └── safety.py          # dB clamps, preview mode
├── scripts/
│   ├── probe.py               # empirical discovery script
│   ├── dump_state.py          # connect, snapshot, print
│   └── repl.py                # async REPL for live prodding
├── tests/
│   ├── unit/
│   │   ├── test_codec.py
│   │   ├── test_cache.py
│   │   ├── test_macros.py
│   │   └── test_scenes.py
│   └── integration/
│       └── test_live_dm3.py   # @pytest.mark.live_hardware
└── data/
    └── DM3 Parameters-2.txt   # vendored; parsed at import
```

## RCP client (`src/dm3_mcp/rcp/client.py`)

```python
class RcpClient:
    def __init__(self, host: str, port: int = 49280): ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...

    async def set(self, address: str, x: int, y: int, value) -> SetResult: ...
    async def get(self, address: str, x: int, y: int) -> GetResult: ...
    async def recall_scene(self, bank: int, scene: int) -> None: ...
    async def store_scene(self, bank: int, scene: int) -> None: ...

    def on_notify(self, handler: Callable[[NotifyEvent], Awaitable[None]]) -> None: ...
```

### Internals

- **Single persistent TCP socket.** Writer task drains a command queue with 5 ms inter-send gap (matches Companion's `MSG_DELAY`). Reader task loops on `readuntil(b'\n')`.
- **Response correlation.** Each queued command gets a `Future`. Reader matches the next `OK`/`OKm`/`ERROR` positionally (RCP has no message IDs; strict serialization makes this safe).
- **NOTIFY demux.** Lines beginning with `NOTIFY` bypass the future machinery and fan out to registered handlers.
- **Keep-alive** every 10 s via harmless `get` (e.g., `IO:Current/Dev/SystemStatus 0 0`). Timeout → tear down & reconnect.
- **Reconnect with backoff**: 1 → 2 → 5 → 10 s cap. State cache entries flipped to `source: "stale"` during the gap; re-sync on reconnect.
- **Value codec** in `types.py`: dB↔int, pan, string quoting. Tools never touch raw ints.

## Parameter schema (`src/dm3_mcp/rcp/params.py`)

Parse `DM3 Parameters-2.txt` at import time into:

```python
@dataclass
class ParamDef:
    address: str             # "MIXER:Current/InCh/Fader/Level"
    x_max: int               # 16
    y_max: int               # 1
    min: int                 # -32768
    max: int                 # 1000
    default: int | str
    unit: str                # "dB"
    type: Literal["integer", "string", "binary", "bool"]
    rw: Literal["r", "rw", "w"]
    scale: int               # 100

PARAMS: dict[str, ParamDef]
```

Every tool validates address, X/Y bounds, RW flag, and value range before sending.

## State cache (`src/dm3_mcp/state/cache.py`)

### Data model — hybrid KV + structured views

```python
class StateCache:
    _values: dict[tuple[str, int, int], CachedValue]

    @dataclass
    class CachedValue:
        value: int | str
        updated_at: float
        source: Literal["init", "set", "notify", "stale"]

    def channel(self, ch: int) -> ChannelView: ...
    def mix(self, mix: int) -> MixView: ...
    def scene(self) -> SceneView: ...
```

Structured views compute on read from the flat store — no duplication.

### Update sources

| Trigger | Write with source |
|---|---|
| `client.set()` returns OK | `"set"` |
| `client.get()` returns | `"init"` |
| `NOTIFY` from surface | `"notify"` |
| Disconnect | all flipped to `"stale"` |

### Initial sync (on connect)

Focused subset, not every parameter:

- Input channel labels, fader levels, mute states, HA gain, 48V (16 × ~5 = 80 gets)
- Mix/matrix/stereo labels + fader states (~20 gets)
- Mute group states (6)
- Current scene number per bank (2)

~110 requests × 5 ms = ~550 ms cold start. PEQ/HPF/LPF/dynamics fetched lazily on first access.

## MCP tool surface

~33 tools. Full shape below; signatures suggestive, not final.

### Meta & connection (4)

- `connect_console(host, port=49280)`
- `disconnect_console()`
- `get_connection_status()`
- `set_safety_mode(mode: "preview" | "live" | "limited")` — `preview` logs without sending; `live` sends without fader clamp; `limited` sends with `max_fader_db` clamp active (default mode at startup)

### Read-only introspection (6) — cache-backed, instant

- `get_channel_state(ch_type, ch_num)` → labels, fader, on, HA, phantom, HPF, sends-by-mix
- `get_mix_state(mix_num)`
- `get_all_labels()` — compact overview
- `get_mute_group_states()`
- `get_current_scene()` → `{bank, number, name_from_metadata}`
- `read_meter(target, num, pickoff)` — bypasses cache

### Primitives (11) — 1:1 RCP wrappers

**Labels & identity**
- `set_channel_label(ch_type, ch_num, name?, color?, icon?, category?)` — 8-char name limit; color/icon/category from DM3 enums.

**Levels & on/off**
- `set_fader_level(target_type, target_num, level_db)` — float dB or `-inf`; clamped [-138.0, +10.0]
- `set_channel_on(target_type, target_num, on)` — Yamaha semantics: on=audible
- `set_mute_group(group_num, active)`
- `set_mute_group_label(group_num, name)`

**Head amp & preamp**
- `set_head_amp_gain(input_ch, gain_db)` — 0–64 dB
- `set_phantom_power(input_ch, on)`

**Routing**
- `set_send(from_type, from_num, to_type, to_num, *, level_db?, on?, pan?, prepost?)` — unified; any subset updated atomically

**DSP**
- `set_hpf(ch_type, ch_num, on, freq_hz?)`
- `set_peq_band(ch_type, ch_num, band, *, freq_hz?, gain_db?, q?, type?)` — band 1–4; `type` enum 0–6 (Bell, L.Shelf, H.Shelf, HPF, LPF variants — exact names confirmed in M0)
- `set_channel_link_group(input_type, ch_num, group)` — 0=NONE, 1–9=A–I

### Macros (6) — batched intents

- `set_mix_exclusive_inputs(mix_num, input_channels, level_db=0)` — *the* "mix 1 = inputs 1,2 only" pattern
- `label_channels(mapping)` — bulk labeling
- `apply_channel_preset(ch_num, preset)` — whole-channel config dict
- `configure_mix_bus(mix_num, *, name?, bus_type?, fader_db?, exclusive_inputs?, send_level_db?)`
- `ramp_fader(target_type, target_num, target_db, duration_ms=500)` — smooth fade, not snap
- `emergency_mute_all(on=True)` — queue-priority bypass

### Scenes (5)

- `list_scenes(bank?, query?, tags?)` — metadata-enriched, orphan-aware
- `recall_scene(bank, number)`
- `recall_scene_by_name(name)` — fuzzy match; errors on ambiguity
- `store_current_as_scene(bank, number, name, description?, tags?)` — writes both console and metadata
- `get_scene_metadata(bank, number)`

### Development (1)

- `run_probe()` — connects, exercises undocumented addresses, captures NOTIFY stream, dumps results to `data/probe-results-<timestamp>.json`

### Tool-shape decisions

- **Enum-typed channel selectors** over one-tool-per-type — reduces tool count, runtime-validated.
- **Macros return structured summaries**: `{mix: 1, enabled: [1,2], disabled: [3..16], failed: []}` so Claude confirms outcomes.
- **All dB values are float** in the API; RCP's `int×100` lives only inside the codec.
- **`set_send` is one tool, not four** — engineer mental model is "change the send," not "change level then on then pan."

### Deferred to v2

- Direct Dante/input patching — pending probe result.
- Dynamics (comp/gate) parameter values — sparse in RCP dump.
- Recorder (USB playback).
- Cue/monitor individual controls — use scenes.

## Scene metadata store

### Location

- Default `~/.dm3-mcp/scenes.json`; override via `DM3_MCP_SCENES_FILE` env or `config.py`.
- Atomic writes (temp file + rename).

### Schema

```json
{
  "version": 1,
  "console": {
    "last_seen_host": "192.168.0.128",
    "last_seen_at": "2026-04-19T18:22:01Z"
  },
  "scenes": {
    "A:5": {
      "bank": "A",
      "number": 5,
      "name": "Worship band 4-piece",
      "description": "Acoustic + bass + keys + vocals, Dante 1-4",
      "tags": ["worship", "band", "4-piece"],
      "created_at": "2026-03-14T19:00:00Z",
      "last_used_at": "2026-04-12T10:30:00Z",
      "use_count": 14,
      "notes": "Kick drum mic on ch 1 only if full band",
      "input_summary": { "1": "Kick", "2": "Bass DI", "3": "Keys L", "4": "Keys R", "5": "Lead Vox" }
    }
  }
}
```

Scene key format: `"<bank>:<number>"`. `input_summary` denormalizes labels at store-time for fast Claude-readable peeks.

### Sync behaviors

- **Store**: `set MIXER:Lib/Bank/Scene/Store <bank> <num>` → on OK, snapshot cached labels into `input_summary`, write metadata, flush.
- **Recall**: `ssrecall_ex` → on OK, update `last_used_at` + `use_count`.
- **Orphan detection** at `list_scenes`: console slot empty but metadata exists → `{orphan: true}`.
- **Console-without-metadata**: populated slot with no local entry → `{has_metadata: false}` so Claude can prompt for naming.

No auto-sweep of all 200 slots on startup. On-demand via `list_scenes(refresh=True)`.

## Safety

Non-negotiable rails on the primitive layer:

1. **Fader dB clamp.** Configurable `max_fader_db` (default +6.0). Above it → `FaderLimitExceeded`. Callers may pass `override_safety=True` explicitly.
2. **Preview mode.** `set_safety_mode("preview")` logs with `[PREVIEW]` prefix, sends nothing. Useful for Claude narrating a plan.
3. **`emergency_mute_all`** — queue-priority bypass, always reachable.
4. **RW enforcement.** `params.py` RW flags checked pre-send; `set` to `r` → `ReadOnlyAddress`.
5. **Serialized sends.** 5 ms gap; no parallelism, no reordering.

Explicitly **not** enforced:
- Feedback prevention (acoustic awareness we don't have).
- Speaker protection beyond fader clamp — human judgment call.

## Error handling

All MCP tools return a consistent envelope: `{ok: bool, data?: {...}, error?: {code, message, detail, retryable}}`.

| Layer | Error types |
|---|---|
| RCP client | `RcpError`, `RcpTimeout`, `ConnectionLost` |
| Schema | `ValidationError(field, acceptable_range)` |
| Cache | Reads carry `{stale: true, last_synced_ago_s: N}` when disconnected |
| Safety | `FaderLimitExceeded`, `ReadOnlyAddress`, `PreviewOnly` |
| Scenes | `SceneNotFound`, `AmbiguousSceneName(candidates)`, `SceneSlotEmpty` |

## Testing strategy

**Unit (no hardware):**
- `test_codec.py` — round-trip every message type including edge cases (strings with spaces, -∞, clamped values).
- `test_cache.py` — writes from set/get/notify, stale handling, structured view correctness.
- `test_params.py` — parameter file parses without loss; RW validation blocks read-only sets.
- `test_macros.py` — mocked client; assert `set_mix_exclusive_inputs(1, [1,2])` produces exactly the expected 16+ primitive calls in order.
- `test_scenes.py` — metadata CRUD, orphan detection, atomic writes.

**Integration (optional, `@pytest.mark.live_hardware`):**
- Real DM3 at configured IP. CI skips. User runs locally before release.
- Snapshot-based: apply known changes, assert cache matches, revert.

**REPL-driven development:**
- `scripts/repl.py` — async IPython with pre-connected `client` and `cache`. Every protocol bug reproduces here first.

## Milestones

### M0 — Empirical probe (first, ~30–45 min with hardware)

Must happen before building macros or cache. Validates the protocol against the real console.

Checklist:
1. TCP 49280 connection, no auth, keep-alive viable.
2. Documented commands work: `set InCh/Fader/Level`, `Label/Name`, `HAGain`, `48VOn`, `ToMix/On`, `ToMix/Level`, `MuteGrpCtrl/On`, `ssrecall_ex`, `MIXER:Lib/Bank/Scene/Store`.
3. Value semantics: -∞ encoding, clamping (`OKm`), string quoting with/without spaces.
4. NOTIFY fires for surface changes: move fader, see it; change patch, see it (or not).
5. 🎯 **Undocumented patch probe**: try `set MIXER:Current/InCh/Patch 0 0 "DANTE1"` / `"AN1"`, `PatchSelect 0 0 1`, `get MIXER:Current/InCh/Patch 0 0`, `get MIXER:Current/InCh/Port 0 0`. Record OK vs ERROR for each.
6. Scene store: does `set MIXER:Lib/Bank/Scene/Store 0 99` overwrite cleanly? Does a recall emit a burst of NOTIFYs we need to absorb?
7. Rate limit: how fast can we send before the DM3 errors?

Output: `data/probe-results-<timestamp>.json`. Informs v1 tool set.

### M1 — RCP client + params + codec

- `RcpClient` with persistent connection, command queue, NOTIFY demux, keep-alive, reconnect.
- `params.py` parsing `DM3 Parameters-2.txt`.
- `types.py` codec (dB, pan, string).
- Unit tests for codec + params.
- `scripts/repl.py` usable.

### M2 — State cache

- In-memory mirror with sources, stale tracking.
- NOTIFY subscription.
- Initial sync sweep on connect.
- Structured views (`channel`, `mix`, `scene`).
- Unit tests against mocked client.

### M3 — Primitive tools

- All 11 primitive tools, each calling `client` + `cache`.
- Safety layer (fader clamp, preview mode, RW enforcement, emergency mute).
- FastMCP registrations in `server.py`.
- Integration test of each primitive against real DM3 (tagged).

### M4 — Macros + scene metadata + scene tools

- 6 macros.
- Scene metadata JSON store with atomic writes.
- 5 scene tools, orphan detection in `list_scenes`.
- Unit tests for macros with mocked client.

### M5 — Polish & release

- README with usage examples.
- `pyproject.toml` publishable.
- Install + `claude mcp add` instructions.
- v1 tag.

## Open questions (resolved during M0)

1. Does DM3 accept undocumented `set MIXER:Current/InCh/Patch 0 0 "DANTE1"`? If yes, direct patching becomes a v1 tool; if no, scene-based is the only path.
2. What NOTIFY subscription behavior does the DM3 have? Is every surface change auto-broadcast, or do we need an explicit subscribe command?
3. Does `set MIXER:Lib/Bank/Scene/Store` support a name/comment parameter, or is naming purely metadata-side?
4. Is 5 ms the right inter-send gap, or can we go faster?

## Risks

- **Probe reveals deeper protocol gaps.** Mitigation: scenes as fallback for anything not directly controllable; the design accommodates this.
- **Scene metadata drift between hosts.** If the user runs the MCP from multiple machines, local JSONs diverge. Acceptable v1 limitation; note in README.
- **NOTIFY flood during scene recall** could saturate the reader. Mitigation: batch NOTIFY updates, apply to cache in groups.
- **Single-connection serialization** caps throughput at ~200 commands/sec. Acceptable for setup workflows; not for real-time performance mixing.
