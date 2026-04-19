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
from datetime import UTC, datetime
from pathlib import Path


async def send_and_read(reader, writer, line: str, timeout: float = 2.0) -> str:
    writer.write((line + "\n").encode())
    await writer.drain()
    try:
        resp = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=timeout)
        return resp.decode().rstrip()
    except TimeoutError:
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
        except TimeoutError:
            break
    return lines


async def run_probe(host: str, port: int, *, auto_yes: bool = False) -> dict:
    results: dict = {
        "host": host,
        "port": port,
        "started_at": datetime.now(UTC).isoformat(),
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
        except TimeoutError:
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
                "UNDOCUMENTED - Rivage-style patch",
            )
        record(
            "get_inch_patch",
            "get MIXER:Current/InCh/Patch 0 0",
            await send_and_read(reader, writer, "get MIXER:Current/InCh/Patch 0 0"),
            "UNDOCUMENTED - can we at least read it?",
        )

        # 12. 🎯 Undocumented DM7-style PatchSelect
        record(
            "set_inch_patchselect",
            "set MIXER:Current/InCh/PatchSelect 0 0 1",
            await send_and_read(
                reader, writer, "set MIXER:Current/InCh/PatchSelect 0 0 1"
            ),
            "UNDOCUMENTED - DM7-style",
        )

        # 13. 🎯 Undocumented InCh/Port (DM7 namespace)
        record(
            "get_inch_port",
            "get MIXER:Current/InCh/Port 0 0",
            await send_and_read(reader, writer, "get MIXER:Current/InCh/Port 0 0"),
            "UNDOCUMENTED - DM7-style port address",
        )

        # 14. Scene: current scene query
        record(
            "sscurrent_a",
            "sscurrent_ex scene_a",
            await send_and_read(reader, writer, "sscurrent_ex scene_a"),
        )

        # 15. Scene: store to B99 (should be safe scratch slot — confirm empty first!)
        if not auto_yes:
            print("\nABOUT TO STORE SCENE B99. Ensure you don't care about that slot.")
            input("Press ENTER to continue or Ctrl-C to abort: ")
        else:
            print("\n[auto-yes] proceeding to store scene B99")
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
        if not auto_yes:
            print("\nMove input fader 1 on the DM3 surface in the next 10 seconds.")
        else:
            print("\n[auto-yes] draining 10s for any unsolicited NOTIFY traffic")
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
            except TimeoutError:
                break
        record(
            "rate_limit_burst_50",
            "<50 back-to-back gets>",
            f"got {count} responses, {errors} errors in {time.monotonic() - t0:.2f}s",
        )

    finally:
        writer.close()
        await writer.wait_closed()

    results["completed_at"] = datetime.now(UTC).isoformat()
    return results


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True, help="DM3 IP address")
    p.add_argument("--port", type=int, default=49280)
    p.add_argument("--out-dir", default="data", help="Directory for results")
    p.add_argument("--yes", action="store_true", help="Skip interactive confirmations")
    args = p.parse_args()

    results = asyncio.run(run_probe(args.host, args.port, auto_yes=args.yes))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"probe-results-{ts}.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
