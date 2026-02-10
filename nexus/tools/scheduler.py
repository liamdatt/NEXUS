from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from dateutil import parser as date_parser

from nexus.db.models import Database
from nexus.tools.base import BaseTool, ToolResult, ToolSpec


ScheduleCallback = Callable[[str, str], Awaitable[None]]

DAY_MAP = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}


class SchedulerTool(BaseTool):
    name = "scheduler"

    def __init__(self, db: Database, scheduler: AsyncIOScheduler, on_fire: ScheduleCallback) -> None:
        self.db = db
        self.scheduler = scheduler
        self.on_fire = on_fire

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Schedule reminders and recurring jobs with list/cancel/update support.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["schedule", "list", "cancel", "update"]},
                    "chat_id": {"type": "string"},
                    "job_id": {"type": "string"},
                    "text": {"type": "string"},
                    "when": {"type": "string"},
                },
                "required": ["action"],
            },
        )

    def _parse_trigger(self, when_text: str):
        lowered = when_text.strip().lower()
        tz = self.scheduler.timezone

        weekly = re.match(r"every\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+(.+)", lowered)
        if weekly:
            day = DAY_MAP[weekly.group(1)]
            dt = date_parser.parse(weekly.group(2))
            return (
                CronTrigger(day_of_week=day, hour=dt.hour, minute=dt.minute, timezone=tz),
                f"weekly on {day} {dt.hour:02d}:{dt.minute:02d}",
                "cron",
            )

        daily = re.match(r"every\s+day\s+at\s+(.+)", lowered)
        if daily:
            dt = date_parser.parse(daily.group(1))
            return (
                CronTrigger(hour=dt.hour, minute=dt.minute, timezone=tz),
                f"daily at {dt.hour:02d}:{dt.minute:02d}",
                "cron",
            )

        weekday = re.match(r"every\s+weekday\s+at\s+(.+)", lowered)
        if weekday:
            dt = date_parser.parse(weekday.group(1))
            return (
                CronTrigger(day_of_week="mon-fri", hour=dt.hour, minute=dt.minute, timezone=tz),
                f"weekdays at {dt.hour:02d}:{dt.minute:02d}",
                "cron",
            )

        dt = date_parser.parse(when_text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return DateTrigger(run_date=dt), dt.isoformat(), "date"

    @staticmethod
    def _next_run_iso(job) -> str | None:
        if not job or not getattr(job, "next_run_time", None):
            return None
        return job.next_run_time.isoformat()

    def _remove_scheduler_job(self, job_id: str) -> None:
        try:
            self.scheduler.remove_job(job_id)
        except JobLookupError:
            pass

    async def _job_wrapper(self, job_id: str, chat_id: str, text: str, one_time: bool) -> None:
        await self.on_fire(chat_id, text)
        if one_time:
            self.db.delete_job(job_id)
            return
        row = self.db.get_job(job_id)
        if not row:
            return
        job = self.scheduler.get_job(job_id)
        self.db.update_job_spec_next_run(job_id, row["spec"], self._next_run_iso(job))

    def restore_jobs(self) -> tuple[int, int]:
        loaded = 0
        failed = 0
        for row in self.db.list_jobs():
            try:
                spec = row.get("spec", {})
                when_text = str(spec.get("when", "")).strip()
                reminder_text = str(spec.get("text", "Reminder"))
                if not when_text:
                    failed += 1
                    continue
                trigger, _summary, kind = self._parse_trigger(when_text)
                job = self.scheduler.add_job(
                    self._job_wrapper,
                    trigger=trigger,
                    id=row["job_id"],
                    kwargs={
                        "job_id": row["job_id"],
                        "chat_id": row["chat_id"],
                        "text": reminder_text,
                        "one_time": kind == "date",
                    },
                    replace_existing=True,
                )
                spec["kind"] = kind
                self.db.update_job_spec_next_run(row["job_id"], spec, self._next_run_iso(job))
                loaded += 1
            except Exception:  # noqa: BLE001
                failed += 1
        return loaded, failed

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = args.get("action")
        chat_id = str(args.get("chat_id") or "").strip()

        if action == "list":
            if not chat_id:
                return ToolResult(ok=False, content="chat_id is required")
            jobs = self.db.list_jobs(chat_id)
            if not jobs:
                return ToolResult(ok=True, content="No scheduled jobs")
            lines = [f"- {job['job_id']} next={job['next_run_at']} spec={job['spec']}" for job in jobs]
            return ToolResult(ok=True, content="\n".join(lines))

        if action == "schedule":
            if not chat_id:
                return ToolResult(ok=False, content="chat_id is required")
            when_text = str(args.get("when") or "").strip()
            reminder_text = str(args.get("text") or "Reminder")
            if not when_text:
                return ToolResult(ok=False, content="when is required")
            try:
                trigger, summary, kind = self._parse_trigger(when_text)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"failed to parse schedule: {exc}")

            job_id = str(uuid4())
            job = self.scheduler.add_job(
                self._job_wrapper,
                trigger=trigger,
                id=job_id,
                kwargs={"job_id": job_id, "chat_id": chat_id, "text": reminder_text, "one_time": kind == "date"},
            )
            self.db.upsert_job(
                job_id=job_id,
                chat_id=chat_id,
                spec={"when": when_text, "text": reminder_text, "kind": kind},
                next_run_at=self._next_run_iso(job),
            )
            return ToolResult(ok=True, content=f"Scheduled job {job_id} ({summary})")

        if action == "cancel":
            job_id = str(args.get("job_id") or "").strip()
            if not job_id:
                return ToolResult(ok=False, content="job_id is required")
            row = self.db.get_job(job_id)
            if row and chat_id and row["chat_id"] != chat_id:
                return ToolResult(ok=False, content="job_id not found for this chat")
            self._remove_scheduler_job(job_id)
            self.db.delete_job(job_id)
            return ToolResult(ok=True, content=f"Cancelled job {job_id}")

        if action == "update":
            job_id = str(args.get("job_id") or "").strip()
            if not job_id:
                return ToolResult(ok=False, content="job_id is required")
            row = self.db.get_job(job_id)
            if not row:
                return ToolResult(ok=False, content=f"Job not found: {job_id}")
            if chat_id and row["chat_id"] != chat_id:
                return ToolResult(ok=False, content="job_id not found for this chat")

            spec = row.get("spec", {})
            when_text = str(args.get("when") or spec.get("when") or "").strip()
            reminder_text = str(args.get("text") or spec.get("text") or "Reminder")
            if not when_text:
                return ToolResult(ok=False, content="when is required")
            try:
                trigger, summary, kind = self._parse_trigger(when_text)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"failed to parse schedule: {exc}")

            self._remove_scheduler_job(job_id)
            job = self.scheduler.add_job(
                self._job_wrapper,
                trigger=trigger,
                id=job_id,
                kwargs={"job_id": job_id, "chat_id": row["chat_id"], "text": reminder_text, "one_time": kind == "date"},
                replace_existing=True,
            )
            updated_spec = {"when": when_text, "text": reminder_text, "kind": kind}
            self.db.update_job_spec_next_run(job_id, updated_spec, self._next_run_iso(job))
            return ToolResult(ok=True, content=f"Updated job {job_id} ({summary})")

        return ToolResult(ok=False, content=f"Unsupported action: {action}")
