"""Live cue/monitor smoke against the DM3."""
import asyncio
import os

from dm3_mcp import server

HOST = os.environ.get("DM3_HOST", "192.168.10.130")


async def demo() -> None:
    print("connect:", await server.connect_console(HOST))
    print("get_active_cue (initial):", await server.get_active_cue())

    print("set_cue inch 1 exclusive:", await server.set_cue("inch", 1, on=True, exclusive=True))
    await asyncio.sleep(0.3)
    print("get_active_cue (after):", await server.get_active_cue())

    print("clear_all_cues:", await server.clear_all_cues())
    print("set_cue_mode in_point=PFL:", await server.set_cue_mode(in_point="PFL"))
    print("set_monitor on=True level=-20:",
          await server.set_monitor(on=True, level_db=-20.0, mono=False))
    print("set_monitor_source mix 1 exclusive:",
          await server.set_monitor_source("mix", 1, on=True, exclusive=True))
    print("disconnect:", await server.disconnect_console())


asyncio.run(demo())
