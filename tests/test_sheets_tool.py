from __future__ import annotations

import asyncio
from pathlib import Path

from nexus.config import Settings
from nexus.tools.sheets import SheetsTool


class _FakeSheetsClient:
    def __init__(self) -> None:
        self.update_called = False
        self.update_calls: list[dict[str, object]] = []
        self.create_called = False

    def create_spreadsheet(self, title: str, sheet_title: str | None = None):
        assert title == "Ops Plan"
        assert sheet_title in {"Sheet1", "Ops"}
        self.create_called = True
        return {
            "spreadsheet_id": "sheet-new",
            "title": title,
            "spreadsheet_url": "https://docs.google.com/spreadsheets/d/sheet-new",
            "sheet_title": sheet_title or "Sheet1",
        }

    def get_values(self, spreadsheet_id: str, range_a1: str):
        assert spreadsheet_id == "sheet-1"
        assert range_a1 == "Tab!A1:B2"
        return {"range": range_a1, "values": [["A", "B"], ["1", "2"]]}

    def update_values(self, spreadsheet_id: str, range_a1: str, values, input_option):  # noqa: ANN001
        self.update_calls.append(
            {
                "spreadsheet_id": spreadsheet_id,
                "range": range_a1,
                "values": values,
                "input_option": input_option,
            }
        )
        self.update_called = True
        return {"updatedRange": range_a1, "updatedRows": len(values), "updatedCells": sum(len(r) for r in values)}

    def append_values(self, spreadsheet_id: str, range_a1: str, values, input_option, insert_option):  # noqa: ANN001
        del spreadsheet_id, range_a1, values, input_option, insert_option
        return {"updates": {"updatedRange": "Tab!A3:B3", "updatedRows": 1, "updatedCells": 2}}

    def clear_values(self, spreadsheet_id: str, range_a1: str):
        del spreadsheet_id
        return {"clearedRange": range_a1}

    def metadata(self, spreadsheet_id: str):
        del spreadsheet_id
        return {"properties": {"title": "Budget"}, "sheets": [{"properties": {}}, {"properties": {}}]}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
    )


def test_sheets_get(tmp_path: Path):
    tool = SheetsTool(_settings(tmp_path), client=_FakeSheetsClient())
    result = asyncio.run(tool.run({"action": "get", "spreadsheet_id": "sheet-1", "range": "Tab!A1:B2"}))
    assert result.ok
    assert "rows=2" in result.content


def test_sheets_update_requires_confirmation(tmp_path: Path):
    tool = SheetsTool(_settings(tmp_path), client=_FakeSheetsClient())
    result = asyncio.run(
        tool.run(
            {
                "action": "update",
                "spreadsheet_id": "sheet-1",
                "range": "Tab!A1:B2",
                "values": [["A", "B"]],
            }
        )
    )
    assert not result.ok
    assert result.requires_confirmation


def test_sheets_update_executes_when_confirmed(tmp_path: Path):
    client = _FakeSheetsClient()
    tool = SheetsTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "update",
                "spreadsheet_id": "sheet-1",
                "range": "Tab!A1:B2",
                "values": [["A", "B"]],
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert client.update_called is True


def test_sheets_create_requires_confirmation(tmp_path: Path):
    tool = SheetsTool(_settings(tmp_path), client=_FakeSheetsClient())
    result = asyncio.run(tool.run({"action": "create", "title": "Ops Plan"}))
    assert not result.ok
    assert result.requires_confirmation


def test_sheets_create_executes_when_confirmed(tmp_path: Path):
    client = _FakeSheetsClient()
    tool = SheetsTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "create",
                "title": "Ops Plan",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert client.create_called is True
    assert "spreadsheet_id=sheet-new" in result.content


def test_sheets_create_with_values_seeds_data(tmp_path: Path):
    client = _FakeSheetsClient()
    tool = SheetsTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "create",
                "title": "Ops Plan",
                "sheet_title": "Ops",
                "values": [["task", "owner"], ["launch", "team"]],
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert client.update_called is True
    assert any(call["range"] == "Ops!A1" for call in client.update_calls)
    assert "Spreadsheet created and seeded." in result.content
