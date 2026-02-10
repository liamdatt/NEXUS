from pathlib import Path

from nexus.config import Settings
from nexus.core.loop import NexusLoop
from nexus.core.policy import PolicyEngine
from nexus.db.models import Database
from nexus.memory.journals import JournalStore
from nexus.memory.store import MemoryStore
from nexus.tools.base import ToolRegistry


class DummyLLM:
    async def complete_json(self, messages, complex_task=False):  # noqa: ANN001
        return {"ok": True, "content": '{"thought":"simple ack","response":"ok"}'}


def test_redacted_log_masks_phone_like_values(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        cli_enabled=False,
    )
    db = Database(settings.db_path)
    loop = NexusLoop(
        settings=settings,
        db=db,
        memory=MemoryStore(settings.memories_dir),
        journals=JournalStore(settings.memories_dir),
        tools=ToolRegistry(),
        policy=PolicyEngine(db),
        llm=DummyLLM(),
    )

    loop._write_redacted_log("test", {"text": "call me at +14155552671"})
    data = (tmp_path / "redacted.log").read_text(encoding="utf-8")
    assert "+14155552671" not in data
    assert "[REDACTED]" in data
