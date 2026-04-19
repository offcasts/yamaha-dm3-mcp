#!/usr/bin/env python3
"""Discovery probe for the actual DM3 scene syntax.

The main probe revealed `set MIXER:Lib/Bank/Scene/Store 1 99` returns WrongFormat
and `ssrecall_ex 1 99` returns InvalidArgument. Try variations.
"""
import argparse
import asyncio
import json
import sys
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


async def run(host: str, port: int) -> dict:
    results = {"host": host, "tests": []}
    reader, writer = await asyncio.open_connection(host, port)

    async def t(name: str, line: str) -> None:
        resp = await send_and_read(reader, writer, line)
        results["tests"].append({"name": name, "sent": line, "response": resp})
        print(f"[{name}] {line!r} -> {resp!r}")

    try:
        # 1) sscurrent_ex variations
        await t("sscurrent_no_arg", "sscurrent_ex")
        await t("sscurrent_a_caps", "sscurrent_ex SCENE_A")
        await t("sscurrent_a_int", "sscurrent_ex 0")
        await t("sscurrent_b_int", "sscurrent_ex 1")
        await t("sscurrent_str_a", 'sscurrent_ex "A"')
        await t("sscurrent_simple", "sscurrent")
        await t("sscurrent_simple_0", "sscurrent 0")

        # 2) ssrecall variations targeting Bank A scene 1 (often safe — most consoles boot to A:1)
        await t("ssrecall_no_underscore_0_1", "ssrecall 0 1")
        await t("ssrecall_ex_0_1", "ssrecall_ex 0 1")
        await t("ssrecall_ex_a_1", 'ssrecall_ex "A" 1')
        await t("ssrecall_ex_scenea_1", "ssrecall_ex scene_a 1")

        # 3) Store via the prminfo-style address
        # scninfo says: "MIXER:Lib/Bank/Scene/Store" 1 2 0 100 0  -> xmax=1 ymax=2 min=0 max=100
        # Try: x=0, y=bank, value=scene
        await t("store_addr_0_1_99", "set MIXER:Lib/Bank/Scene/Store 0 1 99")
        await t("store_addr_0_0_99", "set MIXER:Lib/Bank/Scene/Store 0 0 99")
        # Another variant
        await t("store_addr_b99", "set MIXER:Lib/Bank/Scene/Store 1 99 0")

        # 4) Recall variants
        await t("recall_addr_0_1_99", "set MIXER:Lib/Bank/Scene/Recall 0 1 99")

        # 5) Read-back current
        await t("get_current_scene", "get MIXER:Current/Scene 0 0")
        await t("get_lib_scene", "get MIXER:Lib/Scene 0 0")

        # 6) Try sslist/sscount variants for discovery
        await t("scnlist", "scnlist")
        await t("ssinfo", "ssinfo 0 1")
        await t("sslist", "sslist")

        # 7) Try the "device info" namespace
        await t("devinfo", "devinfo")
        await t("modelname", "devinfo modelname")

        # 8) Subscription discovery — how do we get NOTIFY?
        await t("subscribe_all", "subscribe")
        await t("subscribe_ms", "subscribe MIXER:Current/InCh/Fader/Level")

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
    out = Path("data") / f"probe-scenes-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
