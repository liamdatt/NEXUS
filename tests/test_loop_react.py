import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus.config import Settings
from nexus.core.loop import NexusLoop
from nexus.core.policy import PolicyEngine
from nexus.core.protocol import InboundMessage
from nexus.db.models import Database
from nexus.memory.journals import JournalStore
from nexus.memory.store import MemoryStore
from nexus.tools.base import BaseTool, ToolRegistry, ToolResult, ToolSpec


class _SequenceLLM:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls = 0

    async def complete_json(self, messages, complex_task=False):  # noqa: ANN001, ARG002
        idx = min(self.calls, len(self.outputs) - 1)
        content = self.outputs[idx]
        self.calls += 1
        return {"ok": True, "content": content}


class _EchoTool(BaseTool):
    name = "echo"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Echo test tool",
            input_schema={"type": "object", "properties": {"action": {"type": "string"}}},
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = str(args.get("action", ""))
        if action == "need_confirm" and not args.get("confirmed"):
            return ToolResult(
                ok=True,
                content="pending confirmation",
                requires_confirmation=True,
                risk_level="high",
            )
        return ToolResult(ok=True, content=f"obs:{action or 'none'}")


def _settings(tmp_path: Path, *, agent_max_steps: int = 20) -> Settings:
    prompts = tmp_path / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "system.md").write_text("# System\nReturn valid decision JSON.", encoding="utf-8")
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        prompts_dir=prompts,
        skills_dir=tmp_path / "skills",
        agent_max_steps=agent_max_steps,
        cli_enabled=False,
    )


def _inbound(msg_id: str, text: str = "run task") -> InboundMessage:
    return InboundMessage(
        id=msg_id,
        channel="whatsapp",
        chat_id="self@lid",
        sender_id="self@lid",
        is_self_chat=True,
        is_from_me=True,
        text=text,
        timestamp=datetime.now(timezone.utc),
    )


def _build_loop(tmp_path: Path, llm: _SequenceLLM, *, agent_max_steps: int = 20):
    settings = _settings(tmp_path, agent_max_steps=agent_max_steps)
    db = Database(settings.db_path)
    memory = MemoryStore(settings.memories_dir)
    journals = JournalStore(settings.memories_dir)
    policy = PolicyEngine(db)
    tools = ToolRegistry()
    tools.register(_EchoTool())
    loop = NexusLoop(settings, db, memory, journals, tools, policy, llm)
    sent: list[Any] = []

    async def send_whatsapp(msg):  # noqa: ANN001
        sent.append(msg)

    async def send_cli(text):  # noqa: ANN001
        sent.append(text)

    loop.bind_channels(send_whatsapp, send_cli)
    return loop, sent, db, settings


def _audit_events(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT event FROM audit_log ORDER BY id ASC").fetchall()
    return [row[0] for row in rows]


def test_react_multi_step_chain_returns_single_final_response(tmp_path: Path):
    llm = _SequenceLLM(
        [
            '{"thought":"step1","call":{"name":"echo","arguments":{"action":"a"}}}',
            '{"thought":"step2","call":{"name":"echo","arguments":{"action":"b"}}}',
            '{"thought":"done","response":"final answer"}',
        ]
    )
    loop, sent, db, settings = _build_loop(tmp_path, llm)

    asyncio.run(loop.handle_inbound(_inbound("react-1"), trace_id="t-react-1"))

    assert len(sent) == 1
    assert getattr(sent[0], "text", "") == "final answer"
    assert llm.calls == 3
    events = _audit_events(settings.db_path)
    assert "loop.step" in events
    assert "loop.tool_observation" in events


def test_react_stops_on_max_steps(tmp_path: Path):
    llm = _SequenceLLM(['{"thought":"keep going","call":{"name":"echo","arguments":{"action":"a"}}}'])
    loop, sent, _db, _settings_obj = _build_loop(tmp_path, llm, agent_max_steps=2)

    asyncio.run(loop.handle_inbound(_inbound("react-2"), trace_id="t-react-2"))

    assert len(sent) == 1
    assert "maximum reasoning steps" in (getattr(sent[0], "text", "") or "")
    assert llm.calls == 2


def test_react_retries_after_invalid_model_output(tmp_path: Path):
    llm = _SequenceLLM(
        [
            "not valid json",
            '{"thought":"recover","response":"recovered response"}',
        ]
    )
    loop, sent, _db, _settings_obj = _build_loop(tmp_path, llm)

    asyncio.run(loop.handle_inbound(_inbound("react-3"), trace_id="t-react-3"))

    assert len(sent) == 1
    assert getattr(sent[0], "text", "") == "recovered response"
    assert llm.calls == 2


def test_react_confirmation_required_halts_loop(tmp_path: Path):
    llm = _SequenceLLM(
        [
            '{"thought":"ask confirmation","call":{"name":"echo","arguments":{"action":"need_confirm"}}}',
            '{"thought":"should not run","response":"unexpected"}',
        ]
    )
    loop, sent, db, _settings_obj = _build_loop(tmp_path, llm)

    asyncio.run(loop.handle_inbound(_inbound("react-4"), trace_id="t-react-4"))

    assert len(sent) == 1
    assert "Confirmation required" in (getattr(sent[0], "text", "") or "")
    assert llm.calls == 1
    pending = db.get_latest_pending_action("self@lid")
    assert pending is not None


def test_react_thought_is_not_user_visible_or_persisted(tmp_path: Path):
    llm = _SequenceLLM(['{"thought":"SECRET_THOUGHT_SHOULD_NOT_LEAK","response":"safe output"}'])
    loop, sent, _db, settings = _build_loop(tmp_path, llm)

    asyncio.run(loop.handle_inbound(_inbound("react-5"), trace_id="t-react-5"))

    assert len(sent) == 1
    assert "SECRET_THOUGHT_SHOULD_NOT_LEAK" not in (getattr(sent[0], "text", "") or "")

    with sqlite3.connect(settings.db_path) as conn:
        rows = conn.execute("SELECT text FROM messages WHERE role='assistant'").fetchall()
    assistant_text = "\n".join(str(row[0] or "") for row in rows)
    assert "SECRET_THOUGHT_SHOULD_NOT_LEAK" not in assistant_text

    log_path = settings.db_path.parent / "redacted.log"
    if log_path.exists():
        content = log_path.read_text(encoding="utf-8")
        assert "SECRET_THOUGHT_SHOULD_NOT_LEAK" not in content
