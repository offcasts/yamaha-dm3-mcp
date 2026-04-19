"""Live end-to-end smoke against a real DM3.

Exercises the high-level server tools the way Claude would: connect, label,
configure a mix exclusively, fade, list scenes, recall the first scene,
disconnect. Read DM3_HOST from env (defaults to 192.168.10.130 for this LAN).
"""
import asyncio
import os

from dm3_mcp import server

HOST = os.environ.get("DM3_HOST", "192.168.10.130")


async def demo() -> None:
    print("connect:", await server.connect_console(HOST))
    print(
        "label_channels:",
        await server.label_channels(
            {"1": {"name": "Kick"}, "2": {"name": "Snare"}}
        ),
    )
    print(
        "set_mix_exclusive_inputs:",
        await server.set_mix_exclusive_inputs(1, [1, 2], level_db=-6.0),
    )
    print(
        "set_fader_level:",
        await server.set_fader_level("inch", 1, -10.0),
    )
    print("get_all_labels:", await server.get_all_labels())
    print(
        "store_current_as_scene (metadata only):",
        await server.store_current_as_scene("A", 1, "Smoke Test", tags=["smoke"]),
    )
    print(
        "recall_scene_by_name:",
        await server.recall_scene_by_name("Smoke Test"),
    )
    print("disconnect:", await server.disconnect_console())


if __name__ == "__main__":
    asyncio.run(demo())
