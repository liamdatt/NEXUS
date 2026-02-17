from __future__ import annotations

import asyncio
from pathlib import Path

from nexus.config import Settings
from nexus.tools.calendar import CalendarTool


class _FakeCalendarClient:
    def __init__(self) -> None:
        self.last_call = None

    def create_event(self, **kwargs):  # noqa: ANN003
        self.last_call = kwargs
        return {
            "id": "evt-1",
            "html_link": "https://calendar.google.com/event?eid=abc",
            "start": "2026-02-11T15:00:00-08:00",
            "end": "2026-02-11T16:00:00-08:00",
        }

    def list_events(self, **kwargs):  # noqa: ANN003
        self.last_call = kwargs
        return [
            {
                "id": "evt-1",
                "summary": "Team Sync",
                "start": "2026-02-11T15:00:00-08:00",
                "end": "2026-02-11T16:00:00-08:00",
                "color_id": "7",
                "html_link": "https://calendar.google.com/event?eid=abc",
            }
        ]

    def update_event(self, **kwargs):  # noqa: ANN003
        self.last_call = kwargs
        return {
            "id": "evt-1",
            "html_link": "https://calendar.google.com/event?eid=abc",
            "start": "2026-02-11T15:00:00-08:00",
            "end": "2026-02-11T16:00:00-08:00",
        }

    def list_colors(self):
        return {"1": "#a4bdfc", "2": "#7ae7bf"}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        timezone="America/Los_Angeles",
    )


def test_create_event_uses_default_timezone(tmp_path: Path):
    client = _FakeCalendarClient()
    tool = CalendarTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "create_event",
                "title": "Team Sync",
                "start": "2026-02-11 15:00",
                "duration_minutes": 60,
                "attendees": "a@example.com,b@example.com",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert "Calendar event created." in result.content
    assert client.last_call is not None
    assert client.last_call["timezone_name"] == "America/Los_Angeles"
    assert client.last_call["duration_minutes"] == 60
    assert client.last_call["attendees"] == ["a@example.com", "b@example.com"]


def test_create_event_with_explicit_end(tmp_path: Path):
    client = _FakeCalendarClient()
    tool = CalendarTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "create_event",
                "title": "Client Call",
                "start": "2026-02-11T13:00:00-08:00",
                "end": "2026-02-11T13:30:00-08:00",
                "timezone": "America/Los_Angeles",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert client.last_call["end"] == "2026-02-11T13:30:00-08:00"


def test_list_events_reads_without_confirmation(tmp_path: Path):
    client = _FakeCalendarClient()
    tool = CalendarTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "list_events",
                "time_min": "2026-02-11T00:00:00-08:00",
                "time_max": "2026-02-12T00:00:00-08:00",
            }
        )
    )
    assert result.ok
    assert "Team Sync" in result.content


def test_update_event_requires_confirmation(tmp_path: Path):
    client = _FakeCalendarClient()
    tool = CalendarTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "update_event",
                "event_id": "evt-1",
                "title": "Updated",
            }
        )
    )
    assert not result.ok
    assert result.requires_confirmation
