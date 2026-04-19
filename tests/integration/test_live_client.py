"""Live-DM3 integration tests. Enable with DM3_HOST env var.

Run with:  DM3_HOST=192.168.10.130 pytest -m live_hardware -v
"""
import os

import pytest

from dm3_mcp.rcp.client import RcpClient

pytestmark = pytest.mark.live_hardware

HOST = os.environ.get("DM3_HOST")


@pytest.mark.skipif(not HOST, reason="DM3_HOST not set")
@pytest.mark.asyncio
async def test_live_set_and_get_fader():
    client = RcpClient(HOST)
    await client.connect()
    try:
        await client.set("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)
        result = await client.get("MIXER:Current/InCh/Fader/Level", 0, 0)
        assert result.value == -1000
    finally:
        await client.set("MIXER:Current/InCh/Fader/Level", 0, 0, -32768)
        await client.close()


@pytest.mark.skipif(not HOST, reason="DM3_HOST not set")
@pytest.mark.asyncio
async def test_live_label_roundtrip():
    client = RcpClient(HOST)
    await client.connect()
    try:
        await client.set("MIXER:Current/InCh/Label/Name", 0, 0, "LIVE")
        result = await client.get("MIXER:Current/InCh/Label/Name", 0, 0)
        assert result.value == "LIVE"
    finally:
        await client.close()
