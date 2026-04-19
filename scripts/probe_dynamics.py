"""Probe candidate dynamics addresses (Companion module + Yamaha conventions)."""
import asyncio

HOST = "192.168.10.130"
CANDIDATES = [
    "MIXER:Current/InCh/Cmp/On",
    "MIXER:Current/InCh/Cmp/Threshold",
    "MIXER:Current/InCh/Comp/On",
    "MIXER:Current/InCh/Compressor/On",
    "MIXER:Current/InCh/Gate/On",
    "MIXER:Current/InCh/Gate/Threshold",
    "MIXER:Current/InCh/Dynamics/On",
    "MIXER:Current/InCh/Dyn/On",
    "MIXER:Current/InCh/Dyna1/On",
    "MIXER:Current/InCh/Dyna2/On",
    "MIXER:Current/InCh/DynA/On",
    "MIXER:Current/InCh/Cmp/Type",
    "MIXER:Current/InCh/Cmp1/On",
    "MIXER:Current/InCh/CMP/On",
    "MIXER:Current/InCh/GATE/On",
]

async def main():
    r, w = await asyncio.open_connection(HOST, 49280)
    for addr in CANDIDATES:
        line = f"get {addr} 0 0\n"
        w.write(line.encode())
        await w.drain()
        try:
            resp = (await asyncio.wait_for(r.readuntil(b"\n"), 1.0)).decode().rstrip()
        except TimeoutError:
            resp = "<TIMEOUT>"
        marker = "OK" if resp.startswith("OK") else "--"
        print(f"  {marker}  {addr}  ->  {resp}")
    w.close()
    await w.wait_closed()

asyncio.run(main())
