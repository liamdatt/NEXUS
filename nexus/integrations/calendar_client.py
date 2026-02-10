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
    ) -> dict[str, Any]:
        service = self._service()
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

        event = (
            service.events()
            .insert(
                calendarId=self.settings.google_calendar_id or "primary",
                body=body,
                sendUpdates="all" if clean_attendees else "none",
            )
            .execute()
        )
        return {
            "id": str(event.get("id", "")),
            "html_link": str(event.get("htmlLink", "")),
            "start": str(event.get("start", {}).get("dateTime", "")),
            "end": str(event.get("end", {}).get("dateTime", "")),
        }
