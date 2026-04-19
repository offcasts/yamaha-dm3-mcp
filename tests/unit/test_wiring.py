import asyncio

import pytest

from dm3_mcp.rcp.client import RcpClient
from dm3_mcp.state.cache import StateCache
from dm3_mcp.state.wiring import wire_cache_to_client

from .test_client import FakeDM3


@pytest.mark.asyncio
async def test_notify_flows_into_cache():
    async def _handle(reader, writer):
        await asyncio.sleep(0.05)
        writer.write(b'NOTIFY set MIXER:Current/InCh/Fader/Level 2 0 -700 "-7.00"\n')
        await writer.drain()
        await asyncio.sleep(0.5)

    fake = FakeDM3({}, custom_handler=_handle)
    await fake.start()

    try:
        cache = StateCache()
        client = RcpClient("127.0.0.1", fake.port)
        wire_cache_to_client(cache, client)
        await client.connect()
        await asyncio.sleep(0.2)
        await client.close()
        entry = cache.get("MIXER:Current/InCh/Fader/Level", 2, 0)
        assert entry is not None
        assert entry.value == -700
        assert entry.source == "notify"
    finally:
        await fake.stop()
