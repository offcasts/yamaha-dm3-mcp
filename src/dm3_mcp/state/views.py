from __future__ import annotations

from dm3_mcp.rcp.types import raw_to_db

from .cache import StateCache


class ChannelView:
    def __init__(self, cache: StateCache, ch_1based: int):
        self._cache = cache
        self._x = ch_1based - 1

    def _raw(self, address: str) -> int | str | None:
        entry = self._cache.get(address, self._x, 0)
        return entry.value if entry else None

    @property
    def fader_db(self) -> float | None:
        raw = self._raw("MIXER:Current/InCh/Fader/Level")
        return raw_to_db(int(raw)) if raw is not None else None

    @property
    def on(self) -> bool | None:
        raw = self._raw("MIXER:Current/InCh/Fader/On")
        return bool(raw) if raw is not None else None

    @property
    def label(self) -> str | None:
        raw = self._raw("MIXER:Current/InCh/Label/Name")
        return str(raw) if raw is not None else None

    @property
    def ha_gain_db(self) -> int | None:
        raw = self._cache.get("IO:Current/InCh/HAGain", self._x, 0)
        return int(raw.value) if raw else None

    @property
    def phantom_on(self) -> bool | None:
        raw = self._cache.get("IO:Current/InCh/48VOn", self._x, 0)
        return bool(raw.value) if raw else None


class MixView:
    def __init__(self, cache: StateCache, mix_1based: int):
        self._cache = cache
        self._x = mix_1based - 1

    @property
    def label(self) -> str | None:
        entry = self._cache.get("MIXER:Current/Mix/Label/Name", self._x, 0)
        return str(entry.value) if entry else None

    @property
    def fader_db(self) -> float | None:
        entry = self._cache.get("MIXER:Current/Mix/Fader/Level", self._x, 0)
        return raw_to_db(int(entry.value)) if entry else None
