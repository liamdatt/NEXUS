from __future__ import annotations

from typing import Any

from nexus.config import Settings
from nexus.integrations.calendar_client import CalendarClient
from nexus.tools.base import BaseTool, ToolResult, ToolSpec


def _to_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [x.strip() for x in value.replace(";", ",").split(",") if x.strip()]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    return []


class CalendarTool(BaseTool):
    name = "calendar"

    def __init__(self, settings: Settings, client: CalendarClient | None = None) -> None:
        self.settings = settings
        self.client = client or CalendarClient(settings)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Create Google Calendar events in the configured calendar (default: primary).",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create_event"]},
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "duration_minutes": {"type": "integer"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "timezone": {"type": "string"},
                    "attendees": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                },
                "required": ["action", "title", "start"],
            },
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = args.get("action")
        if action != "create_event":
            return ToolResult(ok=False, content=f"Unsupported action: {action}")

        title = str(args.get("title") or "").strip()
        start = args.get("start")
        if not title:
            return ToolResult(ok=False, content="title is required")
        if not start:
            return ToolResult(ok=False, content="start is required")

        end = args.get("end")
        duration_raw = args.get("duration_minutes")
        if duration_raw is not None:
            try:
                duration_minutes = int(duration_raw)
            except (TypeError, ValueError):
                return ToolResult(ok=False, content="duration_minutes must be an integer")
        else:
            duration_minutes = None
        timezone_name = str(args.get("timezone") or self.settings.timezone)
        description = str(args.get("description") or "").strip() or None
        location = str(args.get("location") or "").strip() or None
        attendees = _to_str_list(args.get("attendees"))

        try:
            event = self.client.create_event(
                title=title,
                start=start,
                end=end,
                duration_minutes=duration_minutes,
                description=description,
                location=location,
                attendees=attendees,
                timezone_name=timezone_name,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, content=f"calendar create_event failed: {exc}")

        return ToolResult(
            ok=True,
            content=(
                "Calendar event created.\n"
                f"id={event.get('id')}\n"
                f"start={event.get('start')}\n"
                f"end={event.get('end')}\n"
                f"link={event.get('html_link')}"
            ),
        )
