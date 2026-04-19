#!/usr/bin/env python3
"""Final confirmations: sscurrent_ex after recall, second-connection NOTIFY behaviour."""
import asyncio, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

HOST = "192.168.10.130"
PORT = 49280

async def s(reader, writer, line, t=2.0):
    writer.write((line + "\n").encode()); await writer.drain()
    try:
        return (await asyncio.wait_for(reader.readuntil(b"\n"), t)).decode().rstrip()
    except asyncio.TimeoutError:
        return "<TIMEOUT>"

async def drain(reader, dur):
    out = []
    end = time.monotonic() + dur
    while time.monotonic() < end:
        try:
            line = await asyncio.wait_for(reader.readuntil(b"\n"), end - time.monotonic())
            out.append(line.decode().rstrip())
        except asyncio.TimeoutError:
            break
    return out

async def main():
    results = {"tests": []}
    r1, w1 = await asyncio.open_connection(HOST, PORT)
    r2, w2 = await asyncio.open_connection(HOST, PORT)

    async def t(name, conn_label, line):
        reader, writer = (r1, w1) if conn_label == 1 else (r2, w2)
        resp = await s(reader, writer, line)
        results["tests"].append({"name": name, "conn": conn_label, "sent": line, "response": resp})
        print(f"[c{conn_label}/{name}] {line!r} -> {resp!r}")

    # Connection 1: recall, then check sscurrent_ex
    await t("c1_recall_a1", 1, "ssrecall_ex scene_a 1")
    await asyncio.sleep(0.5)
    # drain notifies on conn 1 from recall
    n1 = await drain(r1, 1.0)
    results["tests"].append({"name": "c1_recall_drain", "lines": n1, "count": len(n1)})
    print(f"[c1_recall_drain] count={len(n1)} sample={n1[:3]}")

    # Try sscurrent_ex
    await t("c1_sscurrent_a", 1, "sscurrent_ex scene_a")
    await t("c1_sscurrent_b", 1, "sscurrent_ex scene_b")

    # Connection 2: write a fader. See if conn 1 sees a NOTIFY.
    await t("c2_set_fader", 2, "set MIXER:Current/InCh/Fader/Level 2 0 -1500")
    print("waiting 2s for cross-connection NOTIFY on conn 1...")
    n2 = await drain(r1, 2.0)
    results["tests"].append({"name": "c1_after_c2_set_drain", "lines": n2, "count": len(n2)})
    print(f"[c1_after_c2_set_drain] count={len(n2)} sample={n2[:5]}")

    # Connection 2: set channel label
    await t("c2_set_label", 2, 'set MIXER:Current/InCh/Label/Name 2 0 "PROBE2"')
    n3 = await drain(r1, 1.5)
    results["tests"].append({"name": "c1_after_c2_label_drain", "lines": n3, "count": len(n3)})
    print(f"[c1_after_c2_label_drain] count={len(n3)} sample={n3[:5]}")

    w1.close(); w2.close()
    await w1.wait_closed(); await w2.wait_closed()
    Path("data").mkdir(exist_ok=True)
    out = Path("data") / f"probe-final-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out}")

asyncio.run(main())
