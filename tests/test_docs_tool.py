from __future__ import annotations

import asyncio
from pathlib import Path

from nexus.config import Settings
from nexus.tools.docs import DocsTool


class _FakeDocsClient:
    def __init__(self) -> None:
        self.create_called = False
        self.append_called = False
        self.replace_called = False

    def cat_document(self, document_id: str):
        assert document_id == "doc-1"
        return {"document_id": "doc-1", "title": "Spec", "text": "Hello world"}

    def export_document(self, document_id: str, format_name: str):
        assert document_id == "doc-1"
        assert format_name == "txt"
        return {"document_id": "doc-1", "format": "txt", "content": "Exported text"}

    def create_document(self, title: str, initial_text: str | None = None):
        assert title == "Launch Plan"
        assert initial_text == "Initial content"
        self.create_called = True
        return {"document_id": "doc-new", "title": title, "text": initial_text}

    def append_text(self, document_id: str, text: str):
        assert document_id == "doc-1"
        assert text == " Added line"
        self.append_called = True
        return {"document_id": document_id, "title": "Spec", "appended_chars": len(text)}

    def replace_text(
        self,
        document_id: str,
        find_text: str,
        replace_text: str,
        match_case: bool = False,
    ):
        assert document_id == "doc-1"
        assert find_text == "world"
        assert replace_text == "team"
        assert match_case is False
        self.replace_called = True
        return {"document_id": document_id, "title": "Spec", "occurrences_changed": 1}


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


def test_docs_create_requires_confirmation(tmp_path: Path):
    tool = DocsTool(_settings(tmp_path), client=_FakeDocsClient())
    result = asyncio.run(
        tool.run(
            {
                "action": "create",
                "title": "Launch Plan",
                "initial_text": "Initial content",
            }
        )
    )
    assert not result.ok
    assert result.requires_confirmation


def test_docs_create_executes_when_confirmed(tmp_path: Path):
    client = _FakeDocsClient()
    tool = DocsTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "create",
                "title": "Launch Plan",
                "initial_text": "Initial content",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert client.create_called is True
    assert "Document created." in result.content


def test_docs_append_requires_confirmation(tmp_path: Path):
    tool = DocsTool(_settings(tmp_path), client=_FakeDocsClient())
    result = asyncio.run(
        tool.run(
            {
                "action": "append_text",
                "document_id": "doc-1",
                "text": " Added line",
            }
        )
    )
    assert not result.ok
    assert result.requires_confirmation


def test_docs_append_executes_when_confirmed(tmp_path: Path):
    client = _FakeDocsClient()
    tool = DocsTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "append_text",
                "document_id": "doc-1",
                "text": " Added line",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert client.append_called is True


def test_docs_replace_text_executes_when_confirmed(tmp_path: Path):
    client = _FakeDocsClient()
    tool = DocsTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "replace_text",
                "document_id": "doc-1",
                "find_text": "world",
                "replace_text": "team",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert client.replace_called is True
    assert "occurrences_changed=1" in result.content
