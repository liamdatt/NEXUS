import asyncio
import json
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
        if action == "artifact":
            return ToolResult(
                ok=True,
                content="artifact ready",
                artifacts=[
                    {
                        "type": "document",
                        "path": str(args.get("artifact_path") or ""),
                        "file_name": "artifact.txt",
                        "mime_type": "text/plain",
                    }
                ],
            )
        if action == "markdown":
            return ToolResult(
                ok=True,
                content=(
                    "## Summary\n\n"
                    "* Item one\n"
                    "•\u2060  \u200bItem two\n"
                    "[Details](https://example.com)"
                ),
            )
        if action == "explode":
            raise RuntimeError("boom")
        return ToolResult(ok=True, content=f"obs:{action or 'none'}")


class _EmailCaptureTool(BaseTool):
    name = "email"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Email capture test tool",
            input_schema={"type": "object", "properties": {"action": {"type": "string"}}},
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:
        self.calls.append(dict(args))
        return ToolResult(ok=True, content="email queued")


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


def _inbound_cli(msg_id: str, text: str = "run task") -> InboundMessage:
    return InboundMessage(
        id=msg_id,
        channel="cli",
        chat_id="cli-user",
        sender_id="cli-user",
        is_self_chat=True,
        is_from_me=True,
        text=text,
        timestamp=datetime.now(timezone.utc),
    )


def _build_loop(
    tmp_path: Path,
    llm: _SequenceLLM,
    *,
    agent_max_steps: int = 20,
    extra_tools: list[BaseTool] | None = None,
):
    settings = _settings(tmp_path, agent_max_steps=agent_max_steps)
    db = Database(settings.db_path)
    memory = MemoryStore(settings.memories_dir)
    journals = JournalStore(settings.memories_dir)
    policy = PolicyEngine(db)
    tools = ToolRegistry()
    tools.register(_EchoTool())
    for tool in extra_tools or []:
        tools.register(tool)
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


def test_whatsapp_response_is_formatted_before_send(tmp_path: Path):
    llm = _SequenceLLM(
        [
            '{"thought":"format","response":"## Headlines\\n\\n* Item one\\n•\\u2060  \\u200bItem two\\n[Read](https://example.com)"}'
        ]
    )
    loop, sent, _db, settings = _build_loop(tmp_path, llm)

    asyncio.run(loop.handle_inbound(_inbound("react-format-wa"), trace_id="t-react-format-wa"))

    assert len(sent) == 1
    text = getattr(sent[0], "text", "") or ""
    assert "## Headlines" not in text
    assert "•" not in text
    assert "*Headlines*" in text
    assert "- Item one" in text
    assert "- Item two" in text
    assert "Read (https://example.com)" in text
    events = _audit_events(settings.db_path)
    assert "outbound.format.applied" in events


def test_cli_response_is_not_formatted(tmp_path: Path):
    raw = "## Headlines\n\n* Item one\n[Read](https://example.com)"
    llm = _SequenceLLM([json.dumps({"thought": "format", "response": raw})])
    loop, sent, _db, settings = _build_loop(tmp_path, llm)

    asyncio.run(loop.handle_inbound(_inbound_cli("react-format-cli"), trace_id="t-react-format-cli"))

    assert len(sent) == 1
    assert sent[0] == raw
    events = _audit_events(settings.db_path)
    assert "outbound.format.applied" not in events


def test_tool_result_text_is_formatted_for_whatsapp(tmp_path: Path):
    llm = _SequenceLLM(['{"thought":"unused","response":"ok"}'])
    loop, sent, _db, settings = _build_loop(tmp_path, llm)

    inbound = _inbound("react-tool-format", text='/tool echo {"action":"markdown"}')
    asyncio.run(loop.handle_inbound(inbound, trace_id="t-react-tool-format"))

    assert len(sent) == 1
    text = getattr(sent[0], "text", "") or ""
    assert "## Summary" not in text
    assert "*Summary*" in text
    assert "- Item one" in text
    assert "- Item two" in text
    assert "Details (https://example.com)" in text
    events = _audit_events(settings.db_path)
    assert "outbound.format.applied" in events


def test_direct_tool_artifact_is_sent_as_attachment(tmp_path: Path):
    llm = _SequenceLLM(['{"thought":"unused","response":"ok"}'])
    loop, sent, _db, _settings_obj = _build_loop(tmp_path, llm)

    artifact = tmp_path / "workspace" / "artifact.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("artifact", encoding="utf-8")

    inbound = _inbound(
        "react-artifact",
        text=f'/tool echo {{"action":"artifact","artifact_path":"{artifact}"}}',
    )
    asyncio.run(loop.handle_inbound(inbound, trace_id="t-react-artifact"))

    assert len(sent) == 1
    message = sent[0]
    assert getattr(message, "attachments", None)
    assert message.attachments[0].path == str(artifact.resolve())


def test_inbound_exception_returns_safe_error_and_continues(tmp_path: Path):
    llm = _SequenceLLM(['{"thought":"unused","response":"ok"}'])
    loop, sent, _db, settings = _build_loop(tmp_path, llm)

    inbound = _inbound("react-explode", text='/tool echo {"action":"explode"}')
    asyncio.run(loop.handle_inbound(inbound, trace_id="t-react-explode"))

    assert len(sent) == 1
    assert "internal processing error" in (getattr(sent[0], "text", "") or "").lower()
    events = _audit_events(settings.db_path)
    assert "inbound.error" in events

    follow_up = _inbound("react-follow-up", text='/tool echo {"action":"a"}')
    asyncio.run(loop.handle_inbound(follow_up, trace_id="t-react-follow-up"))
    assert len(sent) == 2
    assert "obs:a" in (getattr(sent[1], "text", "") or "")


def test_email_attachment_inferred_from_latest_artifact(tmp_path: Path):
    llm = _SequenceLLM(
        [
            '{"thought":"send email","call":{"name":"email","arguments":{"action":"send_email","to":["a@example.com"],"subject":"Dog image","body_text":"Hey here is that dog image"}}}',
            '{"thought":"done","response":"sent"}',
        ]
    )
    email_tool = _EmailCaptureTool()
    loop, sent, db, settings = _build_loop(tmp_path, llm, extra_tools=[email_tool])

    artifact = tmp_path / "workspace" / "generated" / "images" / "dog.png"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"png")

    first = _inbound(
        "react-artifact-seed",
        text=f'/tool echo {{"action":"artifact","artifact_path":"{artifact}"}}',
    )
    asyncio.run(loop.handle_inbound(first, trace_id="t-react-seed"))

    second = _inbound("react-email", text="Can you send this in an email to a@example.com?")
    asyncio.run(loop.handle_inbound(second, trace_id="t-react-email"))

    assert email_tool.calls
    attachments = email_tool.calls[0].get("attachments")
    assert isinstance(attachments, list) and attachments
    assert attachments[0]["path"] == str(artifact.resolve())
    events = _audit_events(settings.db_path)
    assert "tool.attachment_inferred" in events
    assert len(sent) == 2
    assert getattr(sent[-1], "text", "") == "sent"


def test_email_attachment_inference_missing_returns_guidance(tmp_path: Path):
    llm = _SequenceLLM(
        [
            '{"thought":"send email","call":{"name":"email","arguments":{"action":"send_email","to":["a@example.com"],"subject":"Dog image","body_text":"Hey here is that dog image"}}}',
        ]
    )
    email_tool = _EmailCaptureTool()
    loop, sent, _db, settings = _build_loop(tmp_path, llm, extra_tools=[email_tool])

    inbound = _inbound("react-email-missing", text="Send this in an email please.")
    asyncio.run(loop.handle_inbound(inbound, trace_id="t-react-email-missing"))

    assert len(sent) == 1
    assert "couldn't find a recent generated file" in (getattr(sent[0], "text", "") or "").lower()
    assert not email_tool.calls
    events = _audit_events(settings.db_path)
    assert "tool.attachment_inference_missing" in events
