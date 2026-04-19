"""Async TCP client for Yamaha RCP.

M0-confirmed behaviors:
- Single persistent TCP socket on port 49280, no auth.
- Strict serialization: writer drains a queue with 5 ms inter-send gap.
- NOTIFYs (cross-connection / surface) are demuxed to handlers.
- No `subscribe` command needed — NOTIFYs are pushed automatically.
- Scene recall uses string banks ('A'/'B'); no working scene-store command.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .codec import (
    ParsedResponse,
    encode_get,
    encode_set,
    encode_ssrecall,
    parse_response,
)

log = logging.getLogger(__name__)

NotifyHandler = Callable[[ParsedResponse], Awaitable[None]]

MSG_GAP_S = 0.005
KEEPALIVE_S = 10.0
RESPONSE_TIMEOUT_S = 2.0


@dataclass
class SetResult:
    kind: str
    value: int | str | None
    clamped: bool


@dataclass
class GetResult:
    value: int | str | None


class RcpError(Exception):
    def __init__(self, reason: str, command: str):
        super().__init__(f"{reason} (command: {command!r})")
        self.reason = reason
        self.command = command


class RcpTimeout(Exception):  # noqa: N818
    pass


class ConnectionLost(Exception):  # noqa: N818
    pass


class RcpClient:
    def __init__(self, host: str, port: int = 49280):
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: asyncio.Queue[tuple[str, asyncio.Future[ParsedResponse]]] | None = None
        self._notify_handlers: list[NotifyHandler] = []
        self._reader_task: asyncio.Task | None = None
        self._writer_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._current: asyncio.Future[ParsedResponse] | None = None
        self._closing = False

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        self._closing = False
        self._pending = asyncio.Queue()
        self._reader_task = asyncio.create_task(self._read_loop())
        self._writer_task = asyncio.create_task(self._write_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def close(self) -> None:
        self._closing = True
        for task in (self._keepalive_task, self._writer_task, self._reader_task):
            if task:
                task.cancel()
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        if self._pending is not None:
            while not self._pending.empty():
                _, fut = await self._pending.get()
                if not fut.done():
                    fut.set_exception(ConnectionLost("client closed"))

    def on_notify(self, handler: NotifyHandler) -> None:
        self._notify_handlers.append(handler)

    async def set(self, address: str, x: int, y: int, value) -> SetResult:
        line = encode_set(address, x, y, value)
        resp = await self._send(line)
        if resp.kind == "error":
            raise RcpError(resp.message or "unknown", line.strip())
        return SetResult(kind=resp.kind, value=resp.value, clamped=(resp.kind == "okm"))

    async def get(self, address: str, x: int, y: int) -> GetResult:
        line = encode_get(address, x, y)
        resp = await self._send(line)
        if resp.kind == "error":
            raise RcpError(resp.message or "unknown", line.strip())
        return GetResult(value=resp.value)

    async def recall_scene(self, bank: str, scene: int) -> None:
        line = encode_ssrecall(bank, scene)
        resp = await self._send(line, timeout=5.0)
        if resp.kind == "error":
            raise RcpError(resp.message or "unknown", line.strip())

    async def _send(self, line: str, timeout: float = RESPONSE_TIMEOUT_S) -> ParsedResponse:
        if self._writer is None or self._closing or self._pending is None:
            raise ConnectionLost("not connected")
        fut: asyncio.Future[ParsedResponse] = asyncio.get_event_loop().create_future()
        await self._pending.put((line, fut))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError as e:
            raise RcpTimeout(f"no response to {line.strip()!r}") from e

    async def _write_loop(self) -> None:
        try:
            assert self._pending is not None
            while not self._closing:
                line, fut = await self._pending.get()
                self._current = fut
                assert self._writer is not None
                self._writer.write(line.encode())
                await self._writer.drain()
                await asyncio.sleep(MSG_GAP_S)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.exception("writer loop died: %s", e)

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while not self._closing:
                raw = await self._reader.readuntil(b"\n")
                resp = parse_response(raw.decode())
                if resp.kind == "notify":
                    for h in self._notify_handlers:
                        try:
                            await h(resp)
                        except Exception:  # noqa: BLE001
                            log.exception("notify handler failed")
                    continue
                if self._current is not None and not self._current.done():
                    self._current.set_result(resp)
                    self._current = None
        except asyncio.IncompleteReadError:
            pass
        except asyncio.CancelledError:
            pass

    async def _keepalive_loop(self) -> None:
        try:
            while not self._closing:
                await asyncio.sleep(KEEPALIVE_S)
                try:
                    await self._send(
                        encode_get("IO:Current/Dev/SystemStatus", 0, 0),
                        timeout=3.0,
                    )
                except (RcpTimeout, ConnectionLost):
                    log.warning("keepalive failed; connection may be dead")
                except Exception as e:  # noqa: BLE001
                    log.debug("keepalive get returned error (likely benign): %s", e)
        except asyncio.CancelledError:
            pass
