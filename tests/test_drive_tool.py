from __future__ import annotations

import asyncio
from pathlib import Path

from nexus.config import Settings
from nexus.tools.drive import DriveTool


class _FakeDriveClient:
    def search(self, query: str, max_results: int):
        assert query == "invoice"
        assert max_results == 10
        return [
            {
                "id": "file-1",
                "name": "Invoice Q1",
                "mime_type": "application/pdf",
                "modified_time": "2026-02-10T10:00:00Z",
                "web_view_link": "https://drive.google.com/file/d/file-1/view",
            }
        ]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
    )


def test_drive_search(tmp_path: Path):
    tool = DriveTool(_settings(tmp_path), client=_FakeDriveClient())
    result = asyncio.run(tool.run({"action": "search", "query": "invoice", "max_results": 10}))
    assert result.ok
    assert "Invoice Q1" in result.content
