from dm3_mcp.rcp.client import RcpClient
from dm3_mcp.rcp.codec import ParsedResponse

from .cache import StateCache


def wire_cache_to_client(cache: StateCache, client: RcpClient) -> None:
    """Register a NOTIFY handler that mirrors all surface changes into the cache."""

    async def _on_notify(event: ParsedResponse) -> None:
        if event.address is None or event.x is None or event.y is None or event.value is None:
            return
        cache.record_notify(event.address, event.x, event.y, event.value)

    client.on_notify(_on_notify)
