# M0 Probe Findings — DM3 v3.00 @ 192.168.10.130

**Date:** 2026-04-19
**Console firmware:** `OK devinfo version "V3.00"`, `OK devinfo productname "DM3"`
**Probe scripts:** `scripts/probe.py`, `scripts/probe_scenes.py`, `scripts/probe_scenes2.py`, `scripts/probe_final.py`
**Raw results:** `data/probe-results-*.json`, `data/probe-scenes*-*.json`, `data/probe-final-*.json`

## Confirmed (matches design spec)

| Behavior | Evidence |
|---|---|
| TCP 49280, no auth, silent on connect | `baseline_greeting` -> `<silent>` |
| `set MIXER:Current/InCh/Fader/Level <ch> 0 <raw>` works | `set ... 0 0 -1000` -> `OK ... -1000 "-10.00"` |
| `get` works | `get ... 0 0` -> `OK get ... 0 0 -1000` |
| `set MIXER:Current/InCh/Label/Name <ch> 0 "..."` accepts quoted strings (with spaces) | `"Lead Vox"` round-trips |
| `set IO:Current/InCh/HAGain` works (integer dB) | `set ... 0 0 20` -> `OK ... 20 "+20"` |
| `-32768` raw value = -∞ (`"-?"` display) | `set Fader/Level 0 0 -32768` -> `... "-?"` |
| Out-of-range values clamp with `OKm` (not error) | `set Fader/Level 0 0 9999` -> `OKm ... 1000 "10.00"` |
| `set MIXER:Current/InCh/ToMix/On <inch> <mix> <0|1>` works | returned `OK ... 1 "ON"` |
| `set MIXER:Current/MuteGrpCtrl/On <grp> 0 <0|1>` works | returned `OK ... 1 "ON"` |
| Patching is NOT exposed | `MIXER:Current/InCh/Patch`, `PatchSelect`, `Port` all `UnknownAddress` (DANTE1, AN1, USB1 all rejected) |

**Patching verdict (lock in):** scene recall is the ONLY patching strategy on DM3. No tool for direct input-source assignment.

## Surprises that affect implementation

### 1. OK responses include a trailing display-string token

Spec assumed `OK set <addr> <x> <y> <value>`. Actual format:

```
OK  set MIXER:Current/InCh/Fader/Level 0 0 -1000 "-10.00"
OK  set MIXER:Current/InCh/Label/Name 0 0 "Lead Vox" "Lead Vox"
OKm set MIXER:Current/InCh/Fader/Level 0 0 1000 "10.00"
OK  set IO:Current/InCh/HAGain 0 0 20 "+20"
OK  set MIXER:Current/InCh/ToMix/On 0 0 1 "ON"
```

There is always a trailing quoted display string after the raw value. The codec in `parse_response` already tolerates extra tokens (it only reads `tokens[5]` for the value), so no change needed — but document this so the codec's existing behavior is recognised as load-bearing.

### 2. Scene STORE is NOT supported over RCP/OSC

Both probed forms return errors:

| Sent | Response |
|---|---|
| `set MIXER:Lib/Bank/Scene/Store 1 99` | `ERROR set WrongFormat` |
| `set MIXER:Lib/Bank/Scene/Store 0 1 99` | `ERROR set UnknownAddress` |
| `ssstore_ex scene_b 99` | `ERROR unknown UnknownCommand` |

OSC spec confirms: only `ssrecall_ex` and `sscurrent_ex` are exposed. There is no scene-store command in the OSC spec at all. The `MIXER:Lib/Bank/Scene/Store` entry in the `prminfo`/`scninfo` dump appears to be metadata about a console-internal address that is **not network-accessible**.

**Implementation impact:** the planned `store_current_as_scene` MCP tool cannot perform a store. We expose it but it returns an error explaining that scene storing must be done physically on the console, then upserts local metadata so the operator can record their intent. Alternative: drop the tool and document the limitation. Decision below.

**Decision (committed for v0.1):** keep `store_current_as_scene` as a **metadata-only** tool — it does NOT call the console; it records local metadata for an existing scene slot and returns `{ok: True, console_store: False, note: "Scene store must be initiated from the console panel"}`.

### 3. Scene-recall syntax uses STRING bank names, not ints

Spec assumed `ssrecall_ex 0 5` (int bank). Actual:

| Sent | Response |
|---|---|
| `ssrecall_ex 0 1` | `ERROR ssrecall_ex InvalidArgument` |
| `ssrecall_ex scene_a 1` | `OK ssrecall_ex scene_a 1` |

Bank IDs are the literal strings `scene_a` and `scene_b`. The encoder must emit these.

### 4. `sscurrent_ex` requires a previous recall in that bank, in this session

| Sent | Response (after recalling scene_a 1) |
|---|---|
| `sscurrent_ex scene_a` | `OK sscurrent_ex scene_a 1 unmodified` |
| `sscurrent_ex scene_b` | `ERROR sscurrent_ex InvalidArgument` (no recall on B yet) |

The trailing `unmodified` / `modified` indicates whether the surface state still matches the recalled scene.

### 5. NOTIFYs broadcast to OTHER connections only (and only on `set`/recall events)

Two-connection test: connection 2 sent `set Fader/Level 2 0 -1500`, connection 1 received `NOTIFY set MIXER:Current/InCh/Fader/Level 2 0 -1500 "-15.00"`. Connection 2 itself only got the OK (no NOTIFY echo). Therefore:

- Single-client `set`s leave that client's cache responsibility on the OK path; the NOTIFY handler will not see them.
- Cross-client / surface-driven changes DO reach the cache via NOTIFY.
- We did NOT see NOTIFYs for surface-physical changes during the 10-second auto-yes window — but that's because no human moved the surface during that window. Plan to verify with hands-on physical fader move during M3 integration test.

There is no `subscribe` command — `subscribe` returns `UnknownCommand`. NOTIFYs flow automatically once you connect.

### 6. `ssrecall_ex` triggers the session-bound `sscurrent_ex` notify

After `ssrecall_ex scene_a 1` we receive one NOTIFY: `NOTIFY sscurrent_ex scene_a 1 unmodified`. No fader-by-fader burst. So the recall NOTIFY storm risk noted in the design spec is overstated for DM3 — only one notify per recall.

### 7. Rate limit is much higher than the design's 5 ms gap suggests

50 back-to-back gets returned 50 OKs in 0.02 s = ~2500 cmd/s with zero errors. The 5 ms inter-send gap is overkill. Keep it for safety/v1 (matches Companion's `MSG_DELAY`), but mark it as adjustable.

## Updates to the implementation plan

The following plan changes are made inline in `2026-04-19-dm3-mcp-implementation.md` and the design spec:

1. **`encode_ssrecall(bank_str, scene)`** — bank arg is `"scene_a"` / `"scene_b"`, NOT an int. Update `codec.py` and the `RcpClient.recall_scene` signature.
2. **`encode_store_scene` removed** — no working store command.
3. **`store_current_as_scene` MCP tool** — becomes metadata-only. Does not call `RcpClient`. Returns `console_store: False`.
4. **`get_current_scene` MCP tool** — uses `sscurrent_ex scene_a` / `sscurrent_ex scene_b`; both can return `InvalidArgument` until the first recall in this session, in which case the tool returns `{ok: True, bank: None, number: None, note: "no scene recalled this session"}`.
5. **`bank` is encoded as `"A"`/`"B"` at the MCP boundary, mapped to `"scene_a"`/`"scene_b"` internally** — no change to user-visible API.
6. **No NOTIFY-burst absorption needed for recall** — only a single notify is emitted.
