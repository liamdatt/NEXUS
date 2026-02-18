from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

from nexus.config import Settings
from nexus.tools.base import BaseTool, ToolResult, ToolSpec

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _to_rows(value: Any) -> list[list[Any]] | None:
    if isinstance(value, list):
        if not value:
            return []
        if all(isinstance(row, list) for row in value):
            return value
        return [value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return _to_rows(parsed)
    return None


class ExcelTool(BaseTool):
    name = "excel"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Create and edit local Excel workbooks in the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list_sheets", "read", "write_cells", "append_rows", "add_sheet"],
                    },
                    "path": {"type": "string"},
                    "sheet": {"type": "string"},
                    "sheet_name": {"type": "string"},
                    "range": {"type": "string"},
                    "cells": {
                        "oneOf": [
                            {"type": "object"},
                            {"type": "string"},
                        ]
                    },
                    "rows": {
                        "oneOf": [
                            {"type": "array"},
                            {"type": "string"},
                        ]
                    },
                    "confirmed": {"type": "boolean"},
                },
                "required": ["action"],
            },
        )

    def _resolve_workspace_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.settings.workspace / candidate
        resolved = candidate.resolve()
        workspace = self.settings.workspace.resolve()
        if workspace != resolved and workspace not in resolved.parents:
            raise PermissionError("path escapes workspace")
        return resolved

    @staticmethod
    def _artifact(path: Path) -> dict[str, Any]:
        return {
            "type": "document",
            "path": str(path),
            "file_name": path.name,
            "mime_type": XLSX_MIME,
        }

    @staticmethod
    def _write_preview(action: str, details: list[str]) -> str:
        lines = ["Excel write operation requires confirmation.", f"action={action}", *details, "Reply YES to proceed or NO to cancel."]
        return "\n".join(lines)

    @staticmethod
    def _load_cells(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                return parsed
        return None

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = str(args.get("action") or "")
        raw_path = str(args.get("path") or "").strip()

        if action in {"create", "list_sheets", "read", "write_cells", "append_rows", "add_sheet"} and not raw_path:
            return ToolResult(ok=False, content="path is required")

        try:
            file_path = self._resolve_workspace_path(raw_path) if raw_path else None
        except PermissionError as exc:
            return ToolResult(ok=False, content=f"path rejected: {exc}")

        if action == "create":
            assert file_path is not None
            sheet_name = str(args.get("sheet_name") or "Sheet1").strip() or "Sheet1"
            if file_path.suffix.lower() != ".xlsx":
                file_path = file_path.with_suffix(".xlsx")
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._write_preview(
                        action,
                        [
                            f"path={file_path}",
                            f"sheet_name={sheet_name}",
                        ],
                    ),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )
            file_path.parent.mkdir(parents=True, exist_ok=True)
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = sheet_name
            workbook.save(file_path)
            return ToolResult(
                ok=True,
                content=f"Workbook created.\npath={file_path}\nsheet={sheet_name}",
                artifacts=[self._artifact(file_path)],
            )

        if action == "list_sheets":
            assert file_path is not None
            if not file_path.exists():
                return ToolResult(ok=False, content=f"workbook not found: {file_path}")
            workbook = load_workbook(file_path)
            names = workbook.sheetnames
            workbook.close()
            return ToolResult(ok=True, content="Sheets:\n" + "\n".join(f"- {name}" for name in names))

        if action == "read":
            assert file_path is not None
            if not file_path.exists():
                return ToolResult(ok=False, content=f"workbook not found: {file_path}")
            workbook = load_workbook(file_path, data_only=True)
            sheet_name = str(args.get("sheet") or "").strip() or workbook.sheetnames[0]
            if sheet_name not in workbook.sheetnames:
                workbook.close()
                return ToolResult(ok=False, content=f"sheet not found: {sheet_name}")
            ws = workbook[sheet_name]
            range_a1 = str(args.get("range") or "").strip()
            if range_a1:
                cells = ws[range_a1]
                if not isinstance(cells, tuple):
                    rows = [[cells.value]]
                else:
                    rows = [[cell.value for cell in row] for row in cells]
                workbook.close()
                return ToolResult(
                    ok=True,
                    content=(
                        f"Workbook read.\npath={file_path}\nsheet={sheet_name}\nrange={range_a1}\n"
                        f"values={json.dumps(rows, ensure_ascii=False)}"
                    ),
                )

            out_rows: list[list[Any]] = []
            for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 30), max_col=min(ws.max_column, 12)):
                out_rows.append([cell.value for cell in row])
            workbook.close()
            return ToolResult(
                ok=True,
                content=(
                    f"Workbook read.\npath={file_path}\nsheet={sheet_name}\n"
                    f"values={json.dumps(out_rows, ensure_ascii=False)}"
                ),
            )

        if action == "add_sheet":
            assert file_path is not None
            if not file_path.exists():
                return ToolResult(ok=False, content=f"workbook not found: {file_path}")
            sheet_name = str(args.get("sheet_name") or "").strip()
            if not sheet_name:
                return ToolResult(ok=False, content="sheet_name is required")
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._write_preview(action, [f"path={file_path}", f"sheet_name={sheet_name}"]),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )
            workbook = load_workbook(file_path)
            if sheet_name in workbook.sheetnames:
                workbook.close()
                return ToolResult(ok=False, content=f"sheet already exists: {sheet_name}")
            workbook.create_sheet(sheet_name)
            workbook.save(file_path)
            workbook.close()
            return ToolResult(
                ok=True,
                content=f"Sheet added.\npath={file_path}\nsheet={sheet_name}",
                artifacts=[self._artifact(file_path)],
            )

        if action == "write_cells":
            assert file_path is not None
            if not file_path.exists():
                return ToolResult(ok=False, content=f"workbook not found: {file_path}")
            cells = self._load_cells(args.get("cells"))
            if not cells:
                return ToolResult(ok=False, content="cells must be a JSON object mapping cell => value")
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._write_preview(action, [f"path={file_path}", f"cells={len(cells)}"]),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )
            workbook = load_workbook(file_path)
            sheet_name = str(args.get("sheet") or "").strip() or workbook.sheetnames[0]
            if sheet_name not in workbook.sheetnames:
                workbook.close()
                return ToolResult(ok=False, content=f"sheet not found: {sheet_name}")
            ws = workbook[sheet_name]
            for cell_ref, value in cells.items():
                ws[str(cell_ref)] = value
            workbook.save(file_path)
            workbook.close()
            return ToolResult(
                ok=True,
                content=f"Cells updated.\npath={file_path}\nsheet={sheet_name}\nupdated={len(cells)}",
                artifacts=[self._artifact(file_path)],
            )

        if action == "append_rows":
            assert file_path is not None
            if not file_path.exists():
                return ToolResult(ok=False, content=f"workbook not found: {file_path}")
            rows = _to_rows(args.get("rows"))
            if rows is None:
                return ToolResult(ok=False, content="rows must be a 2D array (or JSON string)")
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._write_preview(action, [f"path={file_path}", f"rows={len(rows)}"]),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )
            workbook = load_workbook(file_path)
            sheet_name = str(args.get("sheet") or "").strip() or workbook.sheetnames[0]
            if sheet_name not in workbook.sheetnames:
                workbook.close()
                return ToolResult(ok=False, content=f"sheet not found: {sheet_name}")
            ws = workbook[sheet_name]
            for row in rows:
                ws.append(row)
            workbook.save(file_path)
            workbook.close()
            return ToolResult(
                ok=True,
                content=f"Rows appended.\npath={file_path}\nsheet={sheet_name}\nrows={len(rows)}",
                artifacts=[self._artifact(file_path)],
            )

        return ToolResult(ok=False, content=f"Unsupported action: {action}")
