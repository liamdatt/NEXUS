from __future__ import annotations

import asyncio
from pathlib import Path

from nexus.config import Settings
from nexus.tools.sheets import SheetsTool


class _FakeSheetsClient:
    def __init__(self) -> None:
        self.update_called = False

    def get_values(self, spreadsheet_id: str, range_a1: str):
        assert spreadsheet_id == "sheet-1"
        assert range_a1 == "Tab!A1:B2"
        return {"range": range_a1, "values": [["A", "B"], ["1", "2"]]}

    def update_values(self, spreadsheet_id: str, range_a1: str, values, input_option):  # noqa: ANN001
        assert spreadsheet_id == "sheet-1"
        assert range_a1 == "Tab!A1:B2"
        assert input_option == "USER_ENTERED"
        assert values == [["A", "B"]]
        self.update_called = True
        return {"updatedRange": range_a1, "updatedRows": 1, "updatedCells": 2}

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
