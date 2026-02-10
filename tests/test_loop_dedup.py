import asyncio
from datetime import datetime, timezone
from pathlib import Path

from nexus.config import Settings
from nexus.core.loop import NexusLoop
from nexus.core.policy import PolicyEngine
from nexus.core.protocol import InboundMessage
from nexus.db.models import Database
from nexus.memory.journals import JournalStore
from nexus.memory.store import MemoryStore
from nexus.tools.base import ToolRegistry


class DummyLLM:
    async def complete_json(self, messages, complex_task=False):  # noqa: ANN001
        return {"ok": True, "content": '{"thought":"simple ack","response":"ok"}'}


def _make_loop(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        cli_enabled=False,
    )
    db = Database(settings.db_path)
    loop = NexusLoop(
        settings=settings,
        db=db,
        memory=MemoryStore(settings.memories_dir),
        journals=JournalStore(settings.memories_dir),
        tools=ToolRegistry(),
        policy=PolicyEngine(db),
        llm=DummyLLM(),
    )
    sent = []

    async def send_whatsapp(msg):
        sent.append(msg)

    async def send_cli(text):
        sent.append(text)

    loop.bind_channels(send_whatsapp, send_cli)
    return loop, sent


def test_duplicate_inbound_is_ignored(tmp_path: Path):
    loop, sent = _make_loop(tmp_path)

    inbound = InboundMessage(
        id="dup-1",
        channel="whatsapp",
        chat_id="self@lid",
        sender_id="self@lid",
        is_self_chat=True,
        is_from_me=True,
        text="hello",
        timestamp=datetime.now(timezone.utc),
    )

    asyncio.run(loop.handle_inbound(inbound, trace_id="t1"))
    asyncio.run(loop.handle_inbound(inbound, trace_id="t2"))

    assert len(sent) == 1


def test_empty_payload_is_ignored(tmp_path: Path):
    loop, sent = _make_loop(tmp_path)

    inbound = InboundMessage(
        id="empty-1",
        channel="whatsapp",
        chat_id="self@lid",
        sender_id="self@lid",
        is_self_chat=True,
        is_from_me=True,
        text="",
        media=None,
        timestamp=datetime.now(timezone.utc),
    )

    asyncio.run(loop.handle_inbound(inbound, trace_id="t3"))

    assert sent == []


def test_claim_ledger_is_single_winner(tmp_path: Path):
    loop, _sent = _make_loop(tmp_path)
    first = loop.db.claim_ledger("m-claim", "inbound", "self@lid")
    second = loop.db.claim_ledger("m-claim", "inbound", "self@lid")
    assert first is True
    assert second is False
