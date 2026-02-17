from __future__ import annotations

from typing import Any

from nexus.config import Settings
from nexus.integrations.contacts_client import ContactsClient
from nexus.tools.base import BaseTool, ToolResult, ToolSpec


class ContactsTool(BaseTool):
    name = "contacts"

    def __init__(self, settings: Settings, client: ContactsClient | None = None) -> None:
        self.settings = settings
        self.client = client or ContactsClient(settings)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="List Google Contacts entries.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list"]},
                    "max_results": {"type": "integer"},
                },
                "required": ["action"],
            },
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = args.get("action")
        if action != "list":
            return ToolResult(ok=False, content=f"Unsupported action: {action}")

        try:
            max_results = int(args.get("max_results") or 20)
        except (TypeError, ValueError):
            return ToolResult(ok=False, content="max_results must be an integer")
        max_results = max(1, min(max_results, 200))

        try:
            rows = self.client.list_contacts(max_results=max_results)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, content=f"contacts list failed: {exc}")

        if not rows:
            return ToolResult(ok=True, content="No contacts found.")
        lines = []
        for idx, row in enumerate(rows, start=1):
            display = row.get("display_name") or "(no name)"
            emails = ", ".join([item for item in row.get("emails", []) if item]) or "-"
            phones = ", ".join([item for item in row.get("phones", []) if item]) or "-"
            lines.append(
                f"{idx}. {display}\n"
                f"   emails={emails}\n"
                f"   phones={phones}"
            )
        return ToolResult(ok=True, content="\n".join(lines))
