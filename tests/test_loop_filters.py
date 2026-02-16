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


def _build_loop(tmp_path: Path) -> tuple[NexusLoop, Database, list]:
    settings = Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        cli_enabled=False,
    )

    db = Database(settings.db_path)
    memory = MemoryStore(settings.memories_dir)
    journals = JournalStore(settings.memories_dir)
    policy = PolicyEngine(db)
    tools = ToolRegistry()
    llm = DummyLLM()
    loop = NexusLoop(settings, db, memory, journals, tools, policy, llm)

    sent = []

    async def send_whatsapp(msg):
        sent.append(msg)

    async def send_cli(text):
        sent.append(text)

    loop.bind_channels(send_whatsapp, send_cli)
    return loop, db, sent


def test_whatsapp_non_self_chat_is_ignored(tmp_path: Path):
    loop, _, sent = _build_loop(tmp_path)

    inbound = InboundMessage(
        id="in-1",
        channel="whatsapp",
        chat_id="someone@s.whatsapp.net",
        sender_id="someone@s.whatsapp.net",
        is_self_chat=False,
        is_from_me=False,
        text="hello",
        timestamp=datetime.now(timezone.utc),
    )

    asyncio.run(loop.handle_inbound(inbound, trace_id="trace-1"))
    assert sent == []


def test_whatsapp_self_chat_not_from_me_sender_matches_chat_is_processed(tmp_path: Path):
    loop, _, sent = _build_loop(tmp_path)

    inbound = InboundMessage(
        id="in-2",
        channel="whatsapp",
        chat_id="15551234567@lid",
        sender_id="15551234567@s.whatsapp.net",
        is_self_chat=True,
        is_from_me=False,
        text="hello",
        timestamp=datetime.now(timezone.utc),
    )

    asyncio.run(loop.handle_inbound(inbound, trace_id="trace-2"))
    assert len(sent) == 1
    assert getattr(sent[0], "text", "") == "ok"


def test_whatsapp_self_chat_not_from_me_sender_mismatch_is_ignored(tmp_path: Path):
    loop, _, sent = _build_loop(tmp_path)

    inbound = InboundMessage(
        id="in-3",
        channel="whatsapp",
        chat_id="15551234567@lid",
        sender_id="15557654321@s.whatsapp.net",
        is_self_chat=True,
        is_from_me=False,
        text="hello",
        timestamp=datetime.now(timezone.utc),
    )

    asyncio.run(loop.handle_inbound(inbound, trace_id="trace-3"))
    assert sent == []


def test_whatsapp_self_chat_from_me_is_processed(tmp_path: Path):
    loop, _, sent = _build_loop(tmp_path)

    inbound = InboundMessage(
        id="in-4",
        channel="whatsapp",
        chat_id="self@s.whatsapp.net",
        sender_id="self@s.whatsapp.net",
        is_self_chat=True,
        is_from_me=True,
        text="hello",
        timestamp=datetime.now(timezone.utc),
    )

    asyncio.run(loop.handle_inbound(inbound, trace_id="trace-4"))
    assert len(sent) == 1
    assert getattr(sent[0], "text", "") == "ok"


def test_outbound_echo_is_ignored(tmp_path: Path):
    loop, db, sent = _build_loop(tmp_path)
    db.insert_ledger("out-echo", "outbound", "self@s.whatsapp.net")

    inbound = InboundMessage(
        id="out-echo",
        channel="whatsapp",
        chat_id="self@s.whatsapp.net",
        sender_id="self@s.whatsapp.net",
        is_self_chat=True,
        is_from_me=True,
        text="echo",
        timestamp=datetime.now(timezone.utc),
    )

    asyncio.run(loop.handle_inbound(inbound, trace_id="trace-5"))
    assert sent == []
