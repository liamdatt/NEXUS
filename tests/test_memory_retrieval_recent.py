from pathlib import Path

from nexus.memory.retrieval import list_recent_daily_note_paths


def test_list_recent_daily_note_paths_returns_recency_order(tmp_path: Path):
    memories = tmp_path / "memories"
    memories.mkdir()
    (memories / "2026-02-01.md").write_text("# 1", encoding="utf-8")
    (memories / "2026-02-03.md").write_text("# 3", encoding="utf-8")
    (memories / "2026-02-02.md").write_text("# 2", encoding="utf-8")
    (memories / "MEMORY.md").write_text("# Long-term", encoding="utf-8")
    (memories / "notes.txt").write_text("ignore", encoding="utf-8")

    paths = list_recent_daily_note_paths(memories, days=2)

    assert [path.name for path in paths] == ["2026-02-03.md", "2026-02-02.md"]


def test_list_recent_daily_note_paths_handles_missing_dir(tmp_path: Path):
    missing = tmp_path / "missing"
    assert list_recent_daily_note_paths(missing, days=5) == []
