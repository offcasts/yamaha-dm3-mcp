from __future__ import annotations

import logging

from dm3_mcp.rcp.client import RcpClient

from .cache import StateCache

log = logging.getLogger(__name__)


# Addresses to prime on connect. Format: (address, x_count, y_count)
INITIAL_SYNC: list[tuple[str, int, int]] = [
    ("MIXER:Current/InCh/Fader/Level", 16, 1),
    ("MIXER:Current/InCh/Fader/On", 16, 1),
    ("MIXER:Current/InCh/Label/Name", 16, 1),
    ("IO:Current/InCh/HAGain", 16, 1),
    ("IO:Current/InCh/48VOn", 16, 1),
    ("MIXER:Current/StInCh/Fader/Level", 2, 1),
    ("MIXER:Current/StInCh/Fader/On", 2, 1),
    ("MIXER:Current/StInCh/Label/Name", 2, 1),
    ("MIXER:Current/Mix/Fader/Level", 6, 1),
    ("MIXER:Current/Mix/Fader/On", 6, 1),
    ("MIXER:Current/Mix/Label/Name", 6, 1),
    ("MIXER:Current/Mtrx/Fader/Level", 2, 1),
    ("MIXER:Current/Mtrx/Label/Name", 2, 1),
    ("MIXER:Current/St/Fader/Level", 1, 1),
    ("MIXER:Current/MuteGrpCtrl/On", 6, 1),
    ("MIXER:Current/MuteGrpCtrl/Label/Name", 6, 1),
    # Cue & Monitor (v0.2)
    ("MIXER:Current/Cue/CueMode", 1, 1),
    ("MIXER:Current/Cue/InCh/Point", 1, 1),
    ("MIXER:Current/Cue/OutCh/Point", 1, 1),
    ("MIXER:Current/Cue/InCh/On", 16, 1),
    ("MIXER:Current/Cue/Mix/On", 6, 1),
    ("MIXER:Current/Cue/St/On", 2, 1),
    ("MIXER:Current/Monitor/On", 1, 1),
    ("MIXER:Current/Monitor/Fader/Level", 1, 1),
    ("MIXER:Current/Monitor/CueInterruption", 1, 1),
    ("MIXER:Current/Monitor/St/MonoMonitor", 1, 1),
]


async def run_initial_sync(client: RcpClient, cache: StateCache) -> int:
    """Fetch the priming subset of parameters. Returns count of entries written."""
    count = 0
    for address, x_max, y_max in INITIAL_SYNC:
        for x in range(x_max):
            for y in range(y_max):
                try:
                    result = await client.get(address, x, y)
                    if result.value is not None:
                        cache.record_init(address, x, y, result.value)
                        count += 1
                except Exception as e:  # noqa: BLE001
                    log.debug("initial sync skip %s[%d,%d]: %s", address, x, y, e)
    return count
