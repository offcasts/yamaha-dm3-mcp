#!/usr/bin/env python3
"""Round 2: confirm ssstore_ex syntax and explore subscribe behaviour."""
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


async def drain(reader, duration_s: float) -> list[str]:
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


async def run(host: str, port: int) -> dict:
    results = {"host": host, "tests": []}
    reader, writer = await asyncio.open_connection(host, port)

    async def t(name: str, line: str, *, timeout: float = 2.0) -> None:
        resp = await send_and_read(reader, writer, line, timeout)
        results["tests"].append({"name": name, "sent": line, "response": resp})
        print(f"[{name}] {line!r} -> {resp!r}")

    try:
        # 1) Confirm ssstore_ex/ssrecall_ex syntax
        await t("ssstore_ex_a99", "ssstore_ex scene_b 99")
        await t("ssrecall_ex_a1", "ssrecall_ex scene_a 1", timeout=5.0)
        # Drain any NOTIFY burst from recall
        burst = await drain(reader, 2.0)
        results["tests"].append({"name": "recall_burst", "lines": burst, "count": len(burst)})
        print(f"[recall_burst] count={len(burst)} sample={burst[:3]}")

        # 2) Recall back to scene_b 99 (since we just stored it), then drain
        await t("ssrecall_ex_b99", "ssrecall_ex scene_b 99", timeout=5.0)
        burst = await drain(reader, 1.5)
        results["tests"].append({"name": "recall_b99_burst", "lines": burst, "count": len(burst)})

        # 3) Subscribe and see what comes through
        await t("subscribe_all", "subscribe")
        sub_burst = await drain(reader, 1.0)
        results["tests"].append({"name": "subscribe_initial", "lines": sub_burst, "count": len(sub_burst)})
        print(f"[subscribe_initial] count={len(sub_burst)} sample={sub_burst[:5]}")

        # 4) After subscribe, change a fader via set; see if we get a NOTIFY echo or only a normal OK
        await t("set_after_subscribe", "set MIXER:Current/InCh/Fader/Level 1 0 -2000")
        burst = await drain(reader, 1.0)
        results["tests"].append({"name": "post_set_traffic", "lines": burst, "count": len(burst)})
        print(f"[post_set_traffic] count={len(burst)} sample={burst[:5]}")

        # 5) Try ssunsubscribe and devinfo prminfo for completeness
        await t("unsubscribe", "unsubscribe")
        await t("devinfo_productname", "devinfo productname")
        await t("devinfo_version", "devinfo version")
        # Also try prminfo dump command
        await t("prminfo_count", "prminfo")

    finally:
        writer.close()
        await writer.wait_closed()

    return results


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=49280)
    args = p.parse_args()
    results = asyncio.run(run(args.host, args.port))
    out = Path("data") / f"probe-scenes2-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
