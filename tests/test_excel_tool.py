from __future__ import annotations

import asyncio
from pathlib import Path

from nexus.config import Settings
from nexus.tools.excel import ExcelTool


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
    )


def test_excel_create_requires_confirmation(tmp_path: Path):
    tool = ExcelTool(_settings(tmp_path))
    result = asyncio.run(tool.run({"action": "create", "path": "book.xlsx"}))
    assert not result.ok
    assert result.requires_confirmation


def test_excel_create_and_write_read(tmp_path: Path):
    tool = ExcelTool(_settings(tmp_path))

    created = asyncio.run(
        tool.run({"action": "create", "path": "book.xlsx", "sheet_name": "Data", "confirmed": True})
    )
    assert created.ok
    assert created.artifacts

    write = asyncio.run(
        tool.run(
            {
                "action": "write_cells",
                "path": "book.xlsx",
                "sheet": "Data",
                "cells": {"A1": "Name", "B1": "Score", "A2": "Liam", "B2": 95},
                "confirmed": True,
            }
        )
    )
    assert write.ok

    read = asyncio.run(tool.run({"action": "read", "path": "book.xlsx", "sheet": "Data", "range": "A1:B2"}))
    assert read.ok
    assert "Liam" in read.content


def test_excel_append_rows(tmp_path: Path):
    tool = ExcelTool(_settings(tmp_path))
    asyncio.run(tool.run({"action": "create", "path": "book.xlsx", "confirmed": True}))
    appended = asyncio.run(
        tool.run(
            {
                "action": "append_rows",
                "path": "book.xlsx",
                "rows": [["A", 1], ["B", 2]],
                "confirmed": True,
            }
        )
    )
    assert appended.ok
