from __future__ import annotations

from typing import Any


__all__ = ["NexusTUI", "run_tui"]


def __getattr__(name: str) -> Any:
    if name == "NexusTUI":
        from nexus.tui.app import NexusTUI

        return NexusTUI
    if name == "run_tui":
        from nexus.tui.app import run_tui

        return run_tui
    raise AttributeError(name)
