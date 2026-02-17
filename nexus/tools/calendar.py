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
            description="List, create, and update Google Calendar events plus color metadata.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list_events", "create_event", "update_event", "colors"]},
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "time_min": {"type": "string"},
                    "time_max": {"type": "string"},
                    "query": {"type": "string"},
                    "event_id": {"type": "string"},
                    "calendar_id": {"type": "string"},
                    "event_color": {"type": "string"},
                    "duration_minutes": {"type": "integer"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "timezone": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "attendees": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                },
                "required": ["action"],
            },
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = args.get("action")
        timezone_name = str(args.get("timezone") or self.settings.timezone)
        calendar_id = str(args.get("calendar_id") or "").strip() or None
        event_color = str(args.get("event_color") or "").strip() or None

        if action == "colors":
            try:
                colors = self.client.list_colors()
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"calendar colors failed: {exc}")
            if not colors:
                return ToolResult(ok=True, content="No calendar colors returned.")
            lines = [
                f"{key}: {value}"
                for key, value in sorted(
                    colors.items(),
                    key=lambda item: int(item[0]) if str(item[0]).isdigit() else 9999,
                )
            ]
            return ToolResult(ok=True, content="Calendar colors:\n" + "\n".join(lines))

        if action == "list_events":
            time_min = args.get("time_min")
            time_max = args.get("time_max")
            if not time_min or not time_max:
                return ToolResult(ok=False, content="time_min and time_max are required for list_events")
            try:
                max_results = int(args.get("max_results") or 25)
            except (TypeError, ValueError):
                return ToolResult(ok=False, content="max_results must be an integer")
            max_results = max(1, min(max_results, 100))
            query = str(args.get("query") or "").strip() or None
            try:
                rows = self.client.list_events(
                    time_min=time_min,
                    time_max=time_max,
                    timezone_name=timezone_name,
                    max_results=max_results,
                    query=query,
                    calendar_id=calendar_id,
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"calendar list_events failed: {exc}")
            if not rows:
                return ToolResult(ok=True, content="No events found in the requested window.")
            lines = []
            for idx, item in enumerate(rows, start=1):
                lines.append(
                    f"{idx}. {item.get('summary') or '(untitled)'}\n"
                    f"   id={item.get('id')}\n"
                    f"   start={item.get('start')}\n"
                    f"   end={item.get('end')}\n"
                    f"   color_id={item.get('color_id') or '-'}\n"
                    f"   link={item.get('html_link') or '-'}"
                )
            return ToolResult(ok=True, content="\n".join(lines))

        if action in {"create_event", "update_event"} and not args.get("confirmed"):
            return ToolResult(
                ok=False,
                content=(
                    "Calendar write operation requires confirmation.\n"
                    f"action={action}\n"
                    "Reply YES to proceed or NO to cancel."
                ),
                requires_confirmation=True,
                risk_level="high",
                proposed_action={"action": action, **args},
            )

        if action == "create_event":
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
                    event_color=event_color,
                    calendar_id=calendar_id,
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

        if action == "update_event":
            event_id = str(args.get("event_id") or "").strip()
            if not event_id:
                return ToolResult(ok=False, content="event_id is required for update_event")
            title_raw = args.get("title")
            title = str(title_raw).strip() if isinstance(title_raw, str) else None
            start = args.get("start")
            end = args.get("end")
            duration_raw = args.get("duration_minutes")
            if duration_raw is not None:
                try:
                    duration_minutes = int(duration_raw)
                except (TypeError, ValueError):
                    return ToolResult(ok=False, content="duration_minutes must be an integer")
            else:
                duration_minutes = None
            description = str(args.get("description") or "").strip() if "description" in args else None
            location = str(args.get("location") or "").strip() if "location" in args else None
            attendees = _to_str_list(args.get("attendees")) if "attendees" in args else None
            try:
                event = self.client.update_event(
                    event_id=event_id,
                    calendar_id=calendar_id,
                    title=title,
                    start=start,
                    end=end,
                    duration_minutes=duration_minutes,
                    description=description,
                    location=location,
                    attendees=attendees,
                    timezone_name=timezone_name,
                    event_color=event_color,
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"calendar update_event failed: {exc}")
            return ToolResult(
                ok=True,
                content=(
                    "Calendar event updated.\n"
                    f"id={event.get('id')}\n"
                    f"start={event.get('start')}\n"
                    f"end={event.get('end')}\n"
                    f"link={event.get('html_link')}"
                ),
            )

        return ToolResult(ok=False, content=f"Unsupported action: {action}")
