import asyncio

import pytest

from dm3_mcp.rcp.client import RcpClient
from dm3_mcp.rcp.codec import ParsedResponse


class FakeDM3:
    """Serves one connection, echoes a canned response per command.

    Pass a `custom_handler` to override the default echo behaviour for tests
    that need to push unsolicited NOTIFY traffic.
    """

    def __init__(self, responses: dict[str, str], custom_handler=None):
        self.responses = responses
        self.received: list[str] = []
        self._server: asyncio.Server | None = None
        self._custom_handler = custom_handler
        self.port = 0

    async def start(self) -> None:
        handler = self._custom_handler or self._handle
        self._server = await asyncio.start_server(handler, host="127.0.0.1", port=0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        # Python 3.12.1+ Server.wait_closed() waits on every open connection task.
        # Our custom test handlers don't all clean up their writers, so cap the
        # wait so the test doesn't hang the event loop.
        try:
            await asyncio.wait_for(self._server.wait_closed(), timeout=1.0)
        except TimeoutError:
            pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                try:
                    line = await reader.readuntil(b"\n")
                except asyncio.IncompleteReadError:
                    break
                cmd = line.decode().rstrip()
                self.received.append(cmd)
                response = self.responses.get(cmd, "ERROR unknown")
                writer.write((response + "\n").encode())
                await writer.drain()
        finally:
            # Python 3.12.1+ tightened Server.wait_closed() to block on open
            # connection tasks; we must close our writer here or fake.stop() hangs.
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
            except (TimeoutError, Exception):
                pass


@pytest.mark.asyncio
async def test_set_returns_ok():
    fake = FakeDM3(
        {
            "set MIXER:Current/InCh/Fader/Level 0 0 -1000": (
                "OK set MIXER:Current/InCh/Fader/Level 0 0 -1000"
            )
        }
    )
    await fake.start()
    try:
        client = RcpClient("127.0.0.1", fake.port)
        await client.connect()
        result = await client.set("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)
        assert result.kind == "ok"
        assert result.value == -1000
        await client.close()
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_get_returns_value():
    fake = FakeDM3(
        {
            "get MIXER:Current/InCh/Fader/Level 0 0": (
                "OK get MIXER:Current/InCh/Fader/Level 0 0 -1000"
            )
        }
    )
    await fake.start()
    try:
        client = RcpClient("127.0.0.1", fake.port)
        await client.connect()
        result = await client.get("MIXER:Current/InCh/Fader/Level", 0, 0)
        assert result.value == -1000
        await client.close()
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_notify_handler_receives_event():
    received: list[ParsedResponse] = []

    async def _handle_with_notify(reader, writer):
        await asyncio.sleep(0.05)
        writer.write(b'NOTIFY set MIXER:Current/InCh/Fader/Level 3 0 -500 "-5.00"\n')
        await writer.drain()
        await asyncio.sleep(0.5)

    fake = FakeDM3({}, custom_handler=_handle_with_notify)
    await fake.start()

    try:
        client = RcpClient("127.0.0.1", fake.port)

        async def handler(ev):
            received.append(ev)

        client.on_notify(handler)
        await client.connect()
        await asyncio.sleep(0.2)
        await client.close()
        assert len(received) == 1
        assert received[0].x == 3
        assert received[0].value == -500
    finally:
        await fake.stop()
