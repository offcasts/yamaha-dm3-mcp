from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["preview", "live", "limited"]


class FaderLimitExceeded(Exception):
    pass


class ReadOnlyAddress(Exception):
    pass


class PreviewOnly(Exception):
    pass


@dataclass
class SafetyContext:
    mode: Mode = "limited"
    max_fader_db: float = 6.0

    def should_send(self) -> bool:
        return self.mode != "preview"

    def check_fader_db(self, db: float, *, override: bool = False) -> None:
        if override:
            return
        if self.mode == "limited" and db > self.max_fader_db:
            raise FaderLimitExceeded(
                f"requested {db} dB exceeds max_fader_db={self.max_fader_db}; "
                "pass override_safety=True to force"
            )
