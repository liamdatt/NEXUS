from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

from nexus.config import Settings
from nexus.integrations.google_auth import load_google_credentials


class CalendarClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _service(self):
        try:
            from googleapiclient.discovery import build  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Google API client dependency missing. Reinstall project dependencies."
            ) from exc

        creds = load_google_credentials(self.settings)
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    @staticmethod
    def _to_datetime(value: str | datetime, tz_name: str) -> datetime:
        tz = ZoneInfo(tz_name)
        if isinstance(value, datetime):
            dt = value
        else:
            dt = date_parser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(tz)

    @staticmethod
    def _calendar_event_out(event: dict[str, Any]) -> dict[str, Any]:
        start = event.get("start", {}) if isinstance(event, dict) else {}
        end = event.get("end", {}) if isinstance(event, dict) else {}
        return {
            "id": str(event.get("id", "")),
            "summary": str(event.get("summary", "")),
            "status": str(event.get("status", "")),
            "html_link": str(event.get("htmlLink", "")),
            "start": str(start.get("dateTime") or start.get("date") or ""),
            "end": str(end.get("dateTime") or end.get("date") or ""),
            "color_id": str(event.get("colorId", "")),
        }

    def _event_body(
        self,
        *,
        title: str,
        start: str | datetime,
        end: str | datetime | None,
        duration_minutes: int | None,
        description: str | None,
        location: str | None,
        attendees: list[str] | None,
        timezone_name: str,
        event_color: str | None = None,
    ) -> dict[str, Any]:
        start_dt = self._to_datetime(start, timezone_name)
        if end is not None:
            end_dt = self._to_datetime(end, timezone_name)
        else:
            minutes = duration_minutes if duration_minutes and duration_minutes > 0 else 60
            end_dt = start_dt + timedelta(minutes=minutes)

        body: dict[str, Any] = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": timezone_name},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        clean_attendees = [email.strip() for email in (attendees or []) if email and email.strip()]
        if clean_attendees:
            body["attendees"] = [{"email": email} for email in clean_attendees]
        if event_color:
            body["colorId"] = str(event_color).strip()
        return body

    def list_events(
        self,
        *,
        time_min: str | datetime,
        time_max: str | datetime,
        timezone_name: str,
        max_results: int,
        query: str | None = None,
        calendar_id: str | None = None,
    ) -> list[dict[str, Any]]:
        service = self._service()
        start = self._to_datetime(time_min, timezone_name)
        end = self._to_datetime(time_max, timezone_name)
        listing = (
            service.events()
            .list(
                calendarId=calendar_id or self.settings.google_calendar_id or "primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                q=query or None,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        out: list[dict[str, Any]] = []
        for item in listing.get("items", []) or []:
            out.append(self._calendar_event_out(item))
        return out

    def create_event(
        self,
        *,
        title: str,
        start: str | datetime,
        end: str | datetime | None,
        duration_minutes: int | None,
        description: str | None,
        location: str | None,
        attendees: list[str] | None,
        timezone_name: str,
        event_color: str | None = None,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        service = self._service()
        body = self._event_body(
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
        clean_attendees = body.get("attendees", [])

        event = (
            service.events()
            .insert(
                calendarId=calendar_id or self.settings.google_calendar_id or "primary",
                body=body,
                sendUpdates="all" if clean_attendees else "none",
            )
            .execute()
        )
        return self._calendar_event_out(event)

    def update_event(
        self,
        *,
        event_id: str,
        calendar_id: str | None = None,
        title: str | None = None,
        start: str | datetime | None = None,
        end: str | datetime | None = None,
        duration_minutes: int | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        timezone_name: str,
        event_color: str | None = None,
    ) -> dict[str, Any]:
        service = self._service()
        resolved_calendar_id = calendar_id or self.settings.google_calendar_id or "primary"
        existing = service.events().get(calendarId=resolved_calendar_id, eventId=event_id).execute()

        body: dict[str, Any] = {
            "summary": str(existing.get("summary", "")),
            "description": str(existing.get("description", "")),
            "location": str(existing.get("location", "")),
            "start": existing.get("start", {}),
            "end": existing.get("end", {}),
        }
        if "attendees" in existing:
            body["attendees"] = existing.get("attendees", [])
        if "colorId" in existing:
            body["colorId"] = existing.get("colorId")

        if title is not None:
            body["summary"] = title
        if description is not None:
            body["description"] = description
        if location is not None:
            body["location"] = location
        if event_color is not None:
            body["colorId"] = str(event_color).strip()
        if attendees is not None:
            clean_attendees = [email.strip() for email in attendees if email and email.strip()]
            body["attendees"] = [{"email": email} for email in clean_attendees]

        if start is not None:
            start_dt = self._to_datetime(start, timezone_name)
            if end is not None:
                end_dt = self._to_datetime(end, timezone_name)
            else:
                minutes = duration_minutes if duration_minutes and duration_minutes > 0 else 60
                end_dt = start_dt + timedelta(minutes=minutes)
            body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": timezone_name}
            body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": timezone_name}
        elif end is not None:
            end_dt = self._to_datetime(end, timezone_name)
            body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": timezone_name}

        event = (
            service.events()
            .update(calendarId=resolved_calendar_id, eventId=event_id, body=body, sendUpdates="all")
            .execute()
        )
        return self._calendar_event_out(event)

    def list_colors(self) -> dict[str, str]:
        service = self._service()
        colors = service.colors().get().execute()
        events = colors.get("event", {}) if isinstance(colors, dict) else {}
        out: dict[str, str] = {}
        for key, value in events.items():
            if not isinstance(value, dict):
                continue
            out[str(key)] = str(value.get("background", ""))
        return out
