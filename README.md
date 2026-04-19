# Yamaha DM3 MCP

An MCP server that lets Claude set up a Yamaha DM3 digital mixer via natural-language prompts: labeling, gain, sends, mute groups, scenes.

## Status

**Design and implementation plan are complete. Code is not yet written.** This repo was handed off from a planning machine (no DM3 access) to a build machine on the same LAN as the DM3. The build machine executes the implementation plan.

## Repository contents

```
├── CLAUDE.md                                          ← auto-loaded by Claude Code; project orientation
├── README.md                                          ← this file
├── .gitignore
├── data/
│   └── DM3 Parameters-2.txt                           ← vendored DM3 RCP parameter dump
├── DM3_osc_extracted.txt                              ← extracted Yamaha OSC spec text
└── docs/superpowers/
    ├── specs/2026-04-19-dm3-mcp-design.md             ← design spec
    └── plans/2026-04-19-dm3-mcp-implementation.md     ← step-by-step implementation plan
```

## Prerequisites (target machine)

- Python 3.11 or later — `python --version`
- `uv` package manager — https://docs.astral.sh/uv/getting-started/installation/
- Git
- **Network access to the DM3 console** on the same LAN
- DM3 configured for external control:
  1. Console: `SETUP` → `NETWORK` → choose "For Mixer Control"
  2. Set Static IP (note the address — you'll need it)
  3. Ping the console from this machine to verify: `ping <DM3_IP>`

## Getting started on the target machine

### 1. Sync this repo

If transferring via git:
```bash
git clone <source-remote> yamaha-dm3-mcp
cd yamaha-dm3-mcp
```

If transferring as a file bundle (tarball, zip, etc.):
```bash
tar -xzf yamaha-dm3-mcp.tar.gz
cd yamaha-dm3-mcp
git log --oneline    # verify the commit history came across
```

### 2. Set the DM3 IP in your shell

```bash
export DM3_HOST=192.168.1.50    # substitute your actual DM3 IP
```

On Windows PowerShell:
```powershell
$env:DM3_HOST = "192.168.1.50"
```

### 3. Open Claude Code here

Launch Claude Code in this directory. `CLAUDE.md` will auto-load and orient the session.

### 4. Execute the plan

Ask Claude Code to execute the implementation plan:

> Execute the implementation plan at docs/superpowers/plans/2026-04-19-dm3-mcp-implementation.md using subagent-driven-development.

Claude will:
- Invoke `superpowers:subagent-driven-development`
- Work through each task in order, dispatching a fresh subagent per task
- Pause between tasks for review
- Commit after each completed task

Alternatively, for inline execution:

> Execute the implementation plan at docs/superpowers/plans/2026-04-19-dm3-mcp-implementation.md using executing-plans.

### 5. Run the empirical probe (Task 4 of the plan)

Milestone 0 is a live-hardware probe that validates protocol assumptions before the rest of the build. It's interactive — you'll be prompted to move a fader on the console during the NOTIFY capture phase.

Expect: ~10 minutes, two confirmation prompts, results written to `data/probe-results-<timestamp>.json`.

### 6. After implementation — connect Claude to the running MCP

Once the plan is executed through Phase 6, install the MCP globally:

```bash
uv pip install -e .
```

Register it with Claude Code:

```bash
claude mcp add yamaha-dm3 -- dm3-mcp
```

Then in any Claude Code session, you can say things like:

- "Connect to the DM3 and tell me what's currently labeled."
- "Set mix 1 to inputs 1 and 2 only at 0 dB."
- "Label channels 1-4 as Kick, Snare, Hi-Hat, Overhead; set phantom on 3 and 4; apply the drum preset."
- "Recall the worship band scene."

## What's in the design

See [docs/superpowers/specs/2026-04-19-dm3-mcp-design.md](docs/superpowers/specs/2026-04-19-dm3-mcp-design.md) for full details. Summary:

- **~33 MCP tools** grouped into: meta (4), read (6), primitives (11), macros (6), scenes (5), dev (1).
- **RCP over TCP port 49280** as the single transport. OSC was evaluated and rejected (narrower coverage, no NOTIFY, no PEQ).
- **Scene-based patching**: direct per-channel Dante patching is almost certainly not exposed via RCP on DM3; scene recall swaps the full input patch in one command.
- **State cache** mirrors the console via `NOTIFY` events so Claude can introspect without round-trips.
- **Safety**: fader dB clamp, preview mode, `emergency_mute_all`.

## Transfer back to the originating machine

If implementation reveals anything that changes the design (e.g., M0 probe finds direct patching *is* supported), commit the updates and push/transfer the repo back. Key files that evolve:

- `docs/superpowers/specs/M0-probe-findings.md` — created on Task 4
- `docs/superpowers/specs/2026-04-19-dm3-mcp-design.md` — updated if reality differs
- `docs/superpowers/plans/2026-04-19-dm3-mcp-implementation.md` — edited inline during execution

## License

TBD (author to decide before v1.0 release).
