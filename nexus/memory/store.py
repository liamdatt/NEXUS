from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from nexus.memory.retrieval import list_recent_daily_note_paths, select_relevant_sections


class MemoryStore:
    def __init__(self, memories_dir: Path, session_window_turns: int = 20) -> None:
        self.memories_dir = memories_dir
        self.memories_dir.mkdir(parents=True, exist_ok=True)
        self.session_window_turns = session_window_turns
        self._session: dict[str, deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=session_window_turns)
        )
        self.memory_file = self.memories_dir / "MEMORY.md"
        if not self.memory_file.exists():
            self.memory_file.write_text("# Long-term Memory\n\n", encoding="utf-8")

    def append_turn(self, chat_id: str, role: str, text: str) -> None:
        self._session[chat_id].append({"role": role, "content": text})

    def session_history(self, chat_id: str) -> list[dict[str, str]]:
        return list(self._session[chat_id])

    def append_long_term_note(self, note: str) -> None:
        with self.memory_file.open("a", encoding="utf-8") as fp:
            fp.write(f"- {note}\n")

    def raw_memory(self) -> str:
        return self.memory_file.read_text(encoding="utf-8")

    def relevant_memory(self, query: str, limit: int = 3) -> list[str]:
        return select_relevant_sections(self.raw_memory(), query=query, limit=limit)

    def recent_daily_notes(self, days: int = 5) -> list[tuple[str, str]]:
        notes: list[tuple[str, str]] = []
        for path in list_recent_daily_note_paths(self.memories_dir, days=days):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            notes.append((path.stem, text))
        return notes
