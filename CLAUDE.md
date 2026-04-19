# Yamaha DM3 MCP — Project Context for Claude Code

**This file is auto-loaded by Claude Code. It establishes orientation for any session in this repo.**

## What this project is

A Python/FastMCP server that exposes a Yamaha DM3 digital mixer to Claude as natural-language tools. The user describes a production setup ("label channels 1-4 as drums, set mix 1 to inputs 1 and 2, recall the worship scene") and Claude translates that into Yamaha RCP commands sent over TCP to the console on the local network.

## Current status

- **Design**: Complete, committed. See [docs/superpowers/specs/2026-04-19-dm3-mcp-design.md](docs/superpowers/specs/2026-04-19-dm3-mcp-design.md).
- **Implementation plan**: Complete, committed. See [docs/superpowers/plans/2026-04-19-dm3-mcp-implementation.md](docs/superpowers/plans/2026-04-19-dm3-mcp-implementation.md).
- **Code**: Not written yet. The plan is the next thing to execute.

## This was handed off to this machine from another machine

The design and planning happened on a different machine that did **not** have access to the DM3. This machine is on the same LAN as the DM3 and will execute the implementation plan — including the empirical probe in Milestone 0 that requires real hardware.

## How to pick up from here

1. **Read the two reference documents in this order:**
   - `docs/superpowers/specs/2026-04-19-dm3-mcp-design.md` — the design spec (what we're building and why)
   - `docs/superpowers/plans/2026-04-19-dm3-mcp-implementation.md` — the step-by-step implementation plan with code

2. **Confirm prerequisites** (listed in the plan's "Prerequisites" section):
   - Python 3.11+, `uv` installed, DM3 reachable on network with "For Mixer Control" enabled.

3. **Execute the plan** using `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`. The plan uses checkbox (`- [ ]`) syntax for progress tracking. Commit after each completed task.

4. **Key tasks that need the live DM3** are marked 🎯 LIVE HARDWARE. Do NOT skip Milestone 0 (Tasks 3-4) — the whole plan's assumptions depend on what the probe discovers.

## Protocol choices already locked in

- **Transport**: RCP over **TCP port 49280** (not OSC/UDP).
- **Language**: Python 3.11+ with FastMCP, `uv` for package management.
- **Architecture**: Layered — `RcpClient` (async TCP) ↔ `StateCache` (in-memory mirror updated from `NOTIFY` events) ↔ FastMCP tools (primitives + macros + scene tools).
- **Patching strategy**: Scene recall, not per-channel patching. Direct input-patch via RCP is almost certainly not supported on DM3 (confirmed read-only on CL/QL, not exposed on DM3's `prminfo` dump). The M0 probe verifies this against real hardware.

## Reference files vendored in this repo

- `data/DM3 Parameters-2.txt` — authoritative dump of the DM3's RCP parameter list (173+ entries), sourced from bitfocus/companion-module-yamaha-rcp. **This is the spec for what the console actually accepts.**
- `DM3_osc_extracted.txt` — extracted text of Yamaha's official OSC specification PDF, kept for cross-reference though we're not using OSC.

## Important external resources

- [bitfocus/companion-module-yamaha-rcp](https://github.com/bitfocus/companion-module-yamaha-rcp) — reference implementation in Node. Port patterns from here if the plan leaves a gap.
- [BrenekH/yamaha-rcp-docs](https://github.com/BrenekH/yamaha-rcp-docs) — community-maintained RCP notes. `research/notify_saver.py` is the empirical-discovery technique our M0 probe mirrors.

## DM3 network config — expected state

- Static IP on the console (SETUP → NETWORK → "For Mixer Control" → Static IP).
- Default console IP in docs is `192.168.0.128`; the actual IP on this network may differ. Export `DM3_HOST` in the shell where tests run:
  ```bash
  export DM3_HOST=192.168.10.130
  ```
- The probe will write to scene slot **B99** as a scratch slot. If you care about existing B99 content on the console, back it up or edit the probe to use a different slot before running Task 4.

## Safety guidelines for live development

- The MCP starts in `limited` safety mode with `max_fader_db = +6.0`. Keep it there during development.
- Physical monitors/speakers should be at low volume when running probe / integration tests — we're about to move faders and toggle mutes on the real console.
- `emergency_mute_all(True)` is always available if something goes wrong.

## Commit style

- One commit per plan task. Plan tasks are numbered; commit messages should reference the feature, not the task number.
- Conventional commit prefixes: `feat(scope): ...`, `test(scope): ...`, `chore: ...`, `docs: ...`, `fix(scope): ...`.
- Hand-editing is fine; the pattern is illustrative.

## When plan diverges from reality

If the M0 probe reveals that the protocol behaves differently than the plan assumes (e.g., NOTIFY timing, scene-store semantics, patch-address responses), update `docs/superpowers/specs/M0-probe-findings.md` with the discovery AND edit the plan inline before continuing. Keep the design spec and plan in sync.
