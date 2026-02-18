from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from nexus.config import Settings
from nexus.tools.pdf import PdfTool


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
    )


def test_pdf_create_requires_confirmation(tmp_path: Path):
    tool = PdfTool(_settings(tmp_path))
    result = asyncio.run(tool.run({"action": "create", "path": "doc.pdf", "text": "hello"}))
    assert not result.ok
    assert result.requires_confirmation


def test_pdf_create_and_extract(tmp_path: Path):
    tool = PdfTool(_settings(tmp_path))
    created = asyncio.run(
        tool.run({"action": "create", "path": "doc.pdf", "title": "Spec", "text": "Hello world", "confirmed": True})
    )
    assert created.ok
    extracted = asyncio.run(tool.run({"action": "extract_text", "path": "doc.pdf"}))
    assert extracted.ok
    assert "Hello world" in extracted.content or "Spec" in extracted.content


def test_pdf_merge(tmp_path: Path):
    tool = PdfTool(_settings(tmp_path))
    asyncio.run(tool.run({"action": "create", "path": "a.pdf", "text": "A", "confirmed": True}))
    asyncio.run(tool.run({"action": "create", "path": "b.pdf", "text": "B", "confirmed": True}))
    merged = asyncio.run(
        tool.run(
            {
                "action": "merge",
                "input_paths": ["a.pdf", "b.pdf"],
                "output_path": "merged.pdf",
                "confirmed": True,
            }
        )
    )
    assert merged.ok


def test_pdf_edit_page_nl_executes(monkeypatch, tmp_path: Path):
    tool = PdfTool(_settings(tmp_path))
    asyncio.run(tool.run({"action": "create", "path": "a.pdf", "text": "A", "confirmed": True}))

    def fake_run(cmd, check, capture_output, text):  # noqa: ANN001
        assert cmd[0] == "nano-pdf"
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr("nexus.tools.pdf.subprocess.run", fake_run)
    result = asyncio.run(
        tool.run(
            {
                "action": "edit_page_nl",
                "path": "a.pdf",
                "output_path": "edited.pdf",
                "page": 1,
                "instruction": "Change title",
                "confirmed": True,
            }
        )
    )
    assert result.ok
