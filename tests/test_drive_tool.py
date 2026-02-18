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

    def upload_file(self, file_path, *, name=None, mime_type=None):  # noqa: ANN001
        assert str(file_path).endswith("workspace/out.txt")
        assert name == "Custom Name"
        assert mime_type == "text/plain"
        return {
            "id": "uploaded-1",
            "name": "Custom Name",
            "mime_type": "text/plain",
            "web_view_link": "https://drive.google.com/file/d/uploaded-1/view",
            "web_content_link": "",
        }


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


def test_drive_upload_requires_confirmation(tmp_path: Path):
    workspace_file = tmp_path / "workspace" / "out.txt"
    workspace_file.parent.mkdir(parents=True, exist_ok=True)
    workspace_file.write_text("hello", encoding="utf-8")

    tool = DriveTool(_settings(tmp_path), client=_FakeDriveClient())
    result = asyncio.run(
        tool.run(
            {
                "action": "upload",
                "path": "out.txt",
                "name": "Custom Name",
                "mime_type": "text/plain",
            }
        )
    )
    assert not result.ok
    assert result.requires_confirmation


def test_drive_upload_executes_when_confirmed(tmp_path: Path):
    workspace_file = tmp_path / "workspace" / "out.txt"
    workspace_file.parent.mkdir(parents=True, exist_ok=True)
    workspace_file.write_text("hello", encoding="utf-8")

    tool = DriveTool(_settings(tmp_path), client=_FakeDriveClient())
    result = asyncio.run(
        tool.run(
            {
                "action": "upload",
                "path": "out.txt",
                "name": "Custom Name",
                "mime_type": "text/plain",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert "Drive upload complete." in result.content
