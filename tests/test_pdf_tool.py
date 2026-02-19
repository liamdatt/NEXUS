from __future__ import annotations

import asyncio
import subprocess
import sys
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


def test_pdf_create_and_extract_and_inspect(tmp_path: Path):
    tool = PdfTool(_settings(tmp_path))
    created = asyncio.run(
        tool.run({"action": "create", "path": "doc.pdf", "title": "Spec", "text": "Hello world", "confirmed": True})
    )
    assert created.ok

    inspected = asyncio.run(tool.run({"action": "inspect", "path": "doc.pdf"}))
    assert inspected.ok
    assert "pages=" in inspected.content

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


def test_pdf_edit_page_nl_auto_fallback_page_index(monkeypatch, tmp_path: Path):
    tool = PdfTool(_settings(tmp_path))
    asyncio.run(tool.run({"action": "create", "path": "a.pdf", "text": "A", "confirmed": True}))
    monkeypatch.setattr(PdfTool, "_nano_pdf_command_prefixes", staticmethod(lambda: [["nano-pdf"]]))

    calls: list[int] = []

    def fake_run(cmd, check, capture_output, text):  # noqa: ANN001
        assert cmd[0] == "nano-pdf"
        page = int(cmd[3])
        calls.append(page)
        if page == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="bad page")
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
                "page_index_mode": "auto",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert calls == [1, 0]


def test_pdf_edit_page_nl_one_based_mode(monkeypatch, tmp_path: Path):
    tool = PdfTool(_settings(tmp_path))
    asyncio.run(tool.run({"action": "create", "path": "a.pdf", "text": "A", "confirmed": True}))
    monkeypatch.setattr(PdfTool, "_nano_pdf_command_prefixes", staticmethod(lambda: [["nano-pdf"]]))

    calls: list[int] = []

    def fake_run(cmd, check, capture_output, text):  # noqa: ANN001
        calls.append(int(cmd[3]))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr("nexus.tools.pdf.subprocess.run", fake_run)
    result = asyncio.run(
        tool.run(
            {
                "action": "edit_page_nl",
                "path": "a.pdf",
                "output_path": "edited.pdf",
                "page": 2,
                "instruction": "Change subtitle",
                "page_index_mode": "one_based",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert calls == [1]


def test_pdf_edit_page_nl_dependency_missing_returns_clean_error(monkeypatch, tmp_path: Path):
    tool = PdfTool(_settings(tmp_path))
    asyncio.run(tool.run({"action": "create", "path": "a.pdf", "text": "A", "confirmed": True}))
    monkeypatch.setattr(PdfTool, "_nano_pdf_command_prefixes", staticmethod(lambda: [["missing-nano-pdf"]]))

    def fake_run(_cmd, check, capture_output, text):  # noqa: ANN001, ARG001
        raise FileNotFoundError("missing")

    monkeypatch.setattr("nexus.tools.pdf.subprocess.run", fake_run)
    result = asyncio.run(
        tool.run(
            {
                "action": "edit_page_nl",
                "path": "a.pdf",
                "output_path": "edited.pdf",
                "page": 1,
                "instruction": "Change title",
                "page_index_mode": "auto",
                "confirmed": True,
            }
        )
    )
    assert not result.ok
    assert "dependency is unavailable" in result.content


def test_pdf_edit_page_nl_uses_module_fallback_when_cli_missing(monkeypatch, tmp_path: Path):
    tool = PdfTool(_settings(tmp_path))
    asyncio.run(tool.run({"action": "create", "path": "a.pdf", "text": "A", "confirmed": True}))
    monkeypatch.setattr(
        PdfTool,
        "_nano_pdf_command_prefixes",
        staticmethod(lambda: [["missing-nano-pdf"], [sys.executable, "-m", "nano_pdf"]]),
    )

    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text):  # noqa: ANN001
        calls.append(cmd)
        if cmd[0] == "missing-nano-pdf":
            raise FileNotFoundError("missing")
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
                "page_index_mode": "auto",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert any(call[0] == "missing-nano-pdf" for call in calls)
    assert any(call[0] == sys.executable and call[1:3] == ["-m", "nano_pdf"] for call in calls)
