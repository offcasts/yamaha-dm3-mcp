from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

Source = Literal["init", "set", "notify", "stale"]


@dataclass
class CachedValue:
    value: int | str
    updated_at: float
    source: Source


class StateCache:
    def __init__(self) -> None:
        self._values: dict[tuple[str, int, int], CachedValue] = {}

    def _record(self, address: str, x: int, y: int, value: int | str, source: Source) -> None:
        self._values[(address, x, y)] = CachedValue(
            value=value, updated_at=time.monotonic(), source=source
        )

    def record_init(self, address: str, x: int, y: int, value: int | str) -> None:
        self._record(address, x, y, value, "init")

    def record_set(self, address: str, x: int, y: int, value: int | str) -> None:
        self._record(address, x, y, value, "set")

    def record_notify(self, address: str, x: int, y: int, value: int | str) -> None:
        self._record(address, x, y, value, "notify")

    def get(self, address: str, x: int, y: int) -> CachedValue | None:
        return self._values.get((address, x, y))

    def mark_all_stale(self) -> None:
        for key, val in self._values.items():
            self._values[key] = CachedValue(
                value=val.value, updated_at=val.updated_at, source="stale"
            )

    def channel(self, ch: int) -> "ChannelView":
        from .views import ChannelView

        return ChannelView(self, ch)

    def mix(self, mix_num: int) -> "MixView":
        from .views import MixView

        return MixView(self, mix_num)
