from __future__ import annotations

import asyncio
from pathlib import Path

from nexus.config import Settings
from nexus.tools.docs import DocsTool


class _FakeDocsClient:
    def cat_document(self, document_id: str):
        assert document_id == "doc-1"
        return {"document_id": "doc-1", "title": "Spec", "text": "Hello world"}

    def export_document(self, document_id: str, format_name: str):
        assert document_id == "doc-1"
        assert format_name == "txt"
        return {"document_id": "doc-1", "format": "txt", "content": "Exported text"}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
    )


def test_docs_cat(tmp_path: Path):
    tool = DocsTool(_settings(tmp_path), client=_FakeDocsClient())
    result = asyncio.run(tool.run({"action": "cat", "document_id": "doc-1"}))
    assert result.ok
    assert "Hello world" in result.content


def test_docs_export(tmp_path: Path):
    tool = DocsTool(_settings(tmp_path), client=_FakeDocsClient())
    result = asyncio.run(tool.run({"action": "export", "document_id": "doc-1", "format": "txt"}))
    assert result.ok
    assert "Exported text" in result.content
