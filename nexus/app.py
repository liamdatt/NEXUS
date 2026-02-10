from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from nexus.channels.cli import CLIChannel
from nexus.channels.ws_client import BridgeClient
from nexus.config import get_settings
from nexus.core.loop import NexusLoop
from nexus.core.policy import PolicyEngine
from nexus.db.models import Database
from nexus.llm.context import ensure_prompt_scaffold
from nexus.llm.router import LLMRouter
from nexus.memory.journals import JournalStore
from nexus.memory.store import MemoryStore
from nexus.tools.base import ToolRegistry
from nexus.tools.calendar import CalendarTool
from nexus.tools.email import EmailTool
from nexus.tools.files import FileSystemTool
from nexus.tools.scheduler import SchedulerTool
from nexus.tools.web import WebTool


logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    settings = get_settings()
    logger.info(
        "Starting Nexus core (bridge=%s, cli_enabled=%s)",
        settings.bridge_ws_url,
        settings.cli_enabled,
    )
    ensure_prompt_scaffold(settings.prompts_dir)

    db = Database(settings.db_path)
    memory = MemoryStore(settings.memories_dir, session_window_turns=settings.session_window_turns)
    journals = JournalStore(settings.memories_dir)
    policy = PolicyEngine(db)
    llm = LLMRouter(settings)

    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    tools = ToolRegistry()
    tools.register(FileSystemTool(settings.workspace))
    tools.register(WebTool(settings))
    tools.register(EmailTool(settings))
    tools.register(CalendarTool(settings))

    loop = NexusLoop(
        settings=settings,
        db=db,
        memory=memory,
        journals=journals,
        tools=tools,
        policy=policy,
        llm=llm,
    )

    scheduler_tool = SchedulerTool(db=db, scheduler=scheduler, on_fire=loop.emit_scheduler_message)
    tools.register(scheduler_tool)

    cli = CLIChannel(prompt=settings.cli_prompt)
    bridge = BridgeClient(
        settings=settings,
        on_inbound=loop.handle_inbound,
        on_delivery=loop.register_outbound_provider_id,
    )

    loop.bind_channels(send_whatsapp=bridge.send_outbound, send_cli=cli.send)

    scheduler.start()
    loaded_jobs, failed_jobs = scheduler_tool.restore_jobs()
    logger.info("Scheduler restore complete; loaded=%s failed=%s", loaded_jobs, failed_jobs)

    tasks = [asyncio.create_task(bridge.run_forever())]
    if settings.cli_enabled:
        tasks.append(asyncio.create_task(cli.run(loop.handle_inbound)))

    try:
        await asyncio.gather(*tasks)
    finally:
        scheduler.shutdown(wait=False)
        await bridge.stop()


if __name__ == "__main__":
    asyncio.run(main())
