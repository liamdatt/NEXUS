from __future__ import annotations

import asyncio
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from nexus.db.models import Database
from nexus.tools.scheduler import SchedulerTool


def test_schedule_list_update_cancel_and_restore(tmp_path: Path):
    async def scenario() -> None:
        db_path = tmp_path / "nexus.db"
        db = Database(db_path)
        fired: list[tuple[str, str]] = []

        async def on_fire(chat_id: str, text: str) -> None:
            fired.append((chat_id, text))

        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.start()
        tool = SchedulerTool(db=db, scheduler=scheduler, on_fire=on_fire)

        try:
            schedule_result = await tool.run({"action": "schedule", "chat_id": "chat-1", "when": "every day at 9:30", "text": "Daily standup"})
            assert schedule_result.ok

            list_result = await tool.run({"action": "list", "chat_id": "chat-1"})
            assert list_result.ok
            assert "Daily standup" in list_result.content

            job_id = db.list_jobs("chat-1")[0]["job_id"]
            update_result = await tool.run({"action": "update", "chat_id": "chat-1", "job_id": job_id, "when": "every weekday at 10:15"})
            assert update_result.ok
            updated = db.get_job(job_id)
            assert updated is not None
            assert "weekday" in updated["spec"]["when"]

            cancel_result = await tool.run({"action": "cancel", "chat_id": "chat-1", "job_id": job_id})
            assert cancel_result.ok
            assert db.get_job(job_id) is None

            # One-time job cleanup after fire.
            once_result = await tool.run({"action": "schedule", "chat_id": "chat-1", "when": "2030-01-01 09:00", "text": "One-time"})
            assert once_result.ok
            one_time_job = db.list_jobs("chat-1")[0]
            await tool._job_wrapper(
                job_id=one_time_job["job_id"],
                chat_id="chat-1",
                text="One-time",
                one_time=True,
            )
            assert db.get_job(one_time_job["job_id"]) is None

            # Restore previously persisted recurring job.
            saved_job_id = "persisted-1"
            db.upsert_job(
                job_id=saved_job_id,
                chat_id="chat-2",
                spec={"when": "every monday at 8:00", "text": "Weekly planning", "kind": "cron"},
                next_run_at=None,
            )
            loaded, failed = tool.restore_jobs()
            assert loaded >= 1
            assert failed == 0
            assert scheduler.get_job(saved_job_id) is not None
        finally:
            scheduler.shutdown(wait=False)

    asyncio.run(scenario())
