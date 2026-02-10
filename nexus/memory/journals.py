from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


class JournalStore:
    def __init__(self, memories_dir: Path) -> None:
        self.memories_dir = memories_dir
        self.memories_dir.mkdir(parents=True, exist_ok=True)

    def append_event(self, line: str) -> Path:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target = self.memories_dir / f"{day}.md"
        if not target.exists():
            target.write_text(f"# Journal {day}\n\n", encoding="utf-8")
        with target.open("a", encoding="utf-8") as fp:
            fp.write(f"- {datetime.now(timezone.utc).isoformat()} {line}\n")
        return target
