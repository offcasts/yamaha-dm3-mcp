from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_scenes_file() -> Path:
    base = os.environ.get("DM3_MCP_SCENES_FILE")
    if base:
        return Path(base)
    return Path.home() / ".dm3-mcp" / "scenes.json"


@dataclass
class Config:
    host: str = os.environ.get("DM3_HOST", "192.168.0.128")
    port: int = int(os.environ.get("DM3_PORT", "49280"))
    max_fader_db: float = float(os.environ.get("DM3_MAX_FADER_DB", "6.0"))
    scenes_file: Path = field(default_factory=_default_scenes_file)
