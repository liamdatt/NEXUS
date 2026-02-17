from __future__ import annotations

from typing import Any

from nexus.config import Settings
from nexus.integrations.drive_client import DriveClient
from nexus.tools.base import BaseTool, ToolResult, ToolSpec


class DriveTool(BaseTool):
    name = "drive"

    def __init__(self, settings: Settings, client: DriveClient | None = None) -> None:
        self.settings = settings
        self.client = client or DriveClient(settings)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Search Google Drive files by query.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["search"]},
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["action"],
            },
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = args.get("action")
        if action != "search":
            return ToolResult(ok=False, content=f"Unsupported action: {action}")

        query = str(args.get("query") or "").strip()
        try:
            max_results = int(args.get("max_results") or 10)
        except (TypeError, ValueError):
            return ToolResult(ok=False, content="max_results must be an integer")
        max_results = max(1, min(max_results, 50))

        try:
            rows = self.client.search(query=query, max_results=max_results)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, content=f"drive search failed: {exc}")

        if not rows:
            return ToolResult(ok=True, content="No matching Drive files found.")
        lines = []
        for idx, row in enumerate(rows, start=1):
            lines.append(
                f"{idx}. {row.get('name') or '(untitled)'}\n"
                f"   id={row.get('id') or '-'}\n"
                f"   type={row.get('mime_type') or '-'}\n"
                f"   modified={row.get('modified_time') or '-'}\n"
                f"   link={row.get('web_view_link') or '-'}"
            )
        return ToolResult(ok=True, content="\n".join(lines))
