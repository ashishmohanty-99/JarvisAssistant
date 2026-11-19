"""Where the sidekick keeps its state (applications, tracker, briefings)."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    path = Path(os.environ.get("SIDEKICK_HOME", "") or Path.home() / ".jarvis-sidekick")
    path.mkdir(parents=True, exist_ok=True)
    return path
