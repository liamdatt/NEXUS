from __future__ import annotations

import json
from typing import Any

from nexus.config import Settings
from nexus.integrations.sheets_client import SheetsClient
from nexus.tools.base import BaseTool, ToolResult, ToolSpec


SCOPE_GUIDANCE = (
    "Google connection is missing required scopes for this action. "
    "Disconnect and reconnect Google from the dashboard."
)


def _normalize_google_error(prefix: str, exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if (
        "insufficient authentication scopes" in lowered
        or "insufficientpermissions" in lowered
        or "insufficient permissions" in lowered
        or "insufficient permission" in lowered
    ):
        return f"{prefix}: {SCOPE_GUIDANCE}"
    return f"{prefix}: {message}"


def _to_values(value: Any) -> list[list[Any]] | None:
    if isinstance(value, list):
        if all(isinstance(row, list) for row in value):
            return value
        return [value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return _to_values(parsed)
    return None


class SheetsTool(BaseTool):
    name = "sheets"

    def __init__(self, settings: Settings, client: SheetsClient | None = None) -> None:
        self.settings = settings
        self.client = client or SheetsClient(settings)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Read and modify Google Sheets values and metadata.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "get", "update", "append", "clear", "metadata"],
                    },
                    "spreadsheet_id": {"type": "string"},
                    "title": {"type": "string"},
                    "sheet_title": {"type": "string"},
                    "range": {"type": "string"},
                    "values": {
                        "oneOf": [
                            {"type": "array"},
                            {"type": "string"},
                        ]
                    },
                    "input_option": {"type": "string"},
                    "insert_option": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["action"],
            },
        )

    @staticmethod
    def _confirmation_preview(action: str, spreadsheet_id: str, range_a1: str) -> str:
        return (
            "Sheets write operation requires confirmation.\n"
            f"action={action}\n"
            f"spreadsheet_id={spreadsheet_id}\n"
            f"range={range_a1}\n"
            "Reply YES to proceed or NO to cancel."
        )

    @staticmethod
    def _create_confirmation_preview(title: str, sheet_title: str, row_count: int) -> str:
        return (
            "Sheets write operation requires confirmation.\n"
            "action=create\n"
            f"title={title}\n"
            f"sheet_title={sheet_title}\n"
            f"seed_rows={row_count}\n"
            "Reply YES to proceed or NO to cancel."
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = str(args.get("action") or "")

        if action == "create":
            title = str(args.get("title") or "").strip()
            if not title:
                return ToolResult(ok=False, content="title is required for create")
            sheet_title = str(args.get("sheet_title") or "Sheet1").strip() or "Sheet1"
            values_supplied = "values" in args and args.get("values") is not None
            values = _to_values(args.get("values")) if values_supplied else None
            if values_supplied and values is None:
                return ToolResult(ok=False, content="values must be a 2D array (or JSON string)")

            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._create_confirmation_preview(
                        title=title,
                        sheet_title=sheet_title,
                        row_count=len(values) if isinstance(values, list) else 0,
                    ),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )

            try:
                created = self.client.create_spreadsheet(title=title, sheet_title=sheet_title)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=_normalize_google_error("sheets create failed", exc))

            spreadsheet_id = str(created.get("spreadsheet_id") or "").strip()
            if not spreadsheet_id:
                return ToolResult(ok=False, content="sheets create failed: missing spreadsheet_id in response")

            resolved_sheet_title = str(created.get("sheet_title") or sheet_title or "Sheet1")
            if values is not None:
                input_option = str(args.get("input_option") or "USER_ENTERED").strip().upper()
                seed_range = f"{resolved_sheet_title}!A1"
                try:
                    updates = self.client.update_values(
                        spreadsheet_id=spreadsheet_id,
                        range_a1=seed_range,
                        values=values,
                        input_option=input_option,
                    )
                except Exception as exc:  # noqa: BLE001
                    return ToolResult(
                        ok=False,
                        content=(
                            f"{_normalize_google_error('sheets create seed failed', exc)}\n"
                            f"spreadsheet_id={spreadsheet_id}\n"
                            f"seed_range={seed_range}"
                        ),
                    )
                return ToolResult(
                    ok=True,
                    content=(
                        "Spreadsheet created and seeded.\n"
                        f"spreadsheet_id={spreadsheet_id}\n"
                        f"title={created.get('title') or title}\n"
                        f"spreadsheet_url={created.get('spreadsheet_url') or '-'}\n"
                        f"seed_range={updates.get('updatedRange') or seed_range}\n"
                        f"updated_rows={updates.get('updatedRows')}\n"
                        f"updated_cells={updates.get('updatedCells')}"
                    ),
                )

            return ToolResult(
                ok=True,
                content=(
                    "Spreadsheet created.\n"
                    f"spreadsheet_id={spreadsheet_id}\n"
                    f"title={created.get('title') or title}\n"
                    f"spreadsheet_url={created.get('spreadsheet_url') or '-'}"
                ),
            )

        spreadsheet_id = str(args.get("spreadsheet_id") or "").strip()
        if not spreadsheet_id:
            return ToolResult(ok=False, content="spreadsheet_id is required")

        range_a1 = str(args.get("range") or "").strip()

        if action == "get":
            if not range_a1:
                return ToolResult(ok=False, content="range is required for get")
            try:
                data = self.client.get_values(spreadsheet_id=spreadsheet_id, range_a1=range_a1)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"sheets get failed: {exc}")
            values = data.get("values", [])
            return ToolResult(
                ok=True,
                content=(
                    f"Sheets range fetched.\n"
                    f"range={data.get('range')}\n"
                    f"rows={len(values) if isinstance(values, list) else 0}\n"
                    f"values={json.dumps(values, ensure_ascii=False)}"
                ),
            )

        if action == "metadata":
            try:
                data = self.client.metadata(spreadsheet_id=spreadsheet_id)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"sheets metadata failed: {exc}")
            title = data.get("properties", {}).get("title")
            sheet_count = len(data.get("sheets", []) or [])
            return ToolResult(ok=True, content=f"Sheets metadata.\ntitle={title}\nsheets={sheet_count}")

        if action in {"update", "append", "clear"}:
            if not range_a1:
                return ToolResult(ok=False, content=f"range is required for {action}")
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._confirmation_preview(action, spreadsheet_id, range_a1),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )

            if action in {"update", "append"}:
                values = _to_values(args.get("values"))
                if values is None:
                    return ToolResult(ok=False, content="values must be a 2D array (or JSON string)")
                input_option = str(args.get("input_option") or "USER_ENTERED").strip().upper()
                if action == "update":
                    try:
                        data = self.client.update_values(
                            spreadsheet_id=spreadsheet_id,
                            range_a1=range_a1,
                            values=values,
                            input_option=input_option,
                        )
                    except Exception as exc:  # noqa: BLE001
                        return ToolResult(ok=False, content=_normalize_google_error("sheets update failed", exc))
                    return ToolResult(
                        ok=True,
                        content=(
                            "Sheets values updated.\n"
                            f"updated_range={data.get('updatedRange')}\n"
                            f"updated_rows={data.get('updatedRows')}\n"
                            f"updated_cells={data.get('updatedCells')}"
                        ),
                    )

                insert_option = str(args.get("insert_option") or "INSERT_ROWS").strip().upper()
                try:
                    data = self.client.append_values(
                        spreadsheet_id=spreadsheet_id,
                        range_a1=range_a1,
                        values=values,
                        input_option=input_option,
                        insert_option=insert_option,
                    )
                except Exception as exc:  # noqa: BLE001
                    return ToolResult(ok=False, content=_normalize_google_error("sheets append failed", exc))
                updates = data.get("updates", {})
                return ToolResult(
                    ok=True,
                    content=(
                        "Sheets values appended.\n"
                        f"updated_range={updates.get('updatedRange')}\n"
                        f"updated_rows={updates.get('updatedRows')}\n"
                        f"updated_cells={updates.get('updatedCells')}"
                    ),
                )

            try:
                data = self.client.clear_values(spreadsheet_id=spreadsheet_id, range_a1=range_a1)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=_normalize_google_error("sheets clear failed", exc))
            return ToolResult(ok=True, content=f"Sheets range cleared.\ncleared_range={data.get('clearedRange')}")

        return ToolResult(ok=False, content=f"Unsupported action: {action}")
