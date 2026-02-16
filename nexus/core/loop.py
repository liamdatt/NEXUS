from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from nexus.config import Settings
from nexus.core.decision import AgentDecision, DecisionParseError, parse_agent_decision
from nexus.core.policy import PolicyEngine
from nexus.core.protocol import InboundMessage, OutboundMessage
from nexus.db.models import Database
from nexus.llm.context import ContextBuilder
from nexus.llm.router import LLMRouter
from nexus.memory.journals import JournalStore
from nexus.memory.store import MemoryStore
from nexus.tools.base import ToolRegistry, ToolResult


logger = logging.getLogger(__name__)


class NexusLoop:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        memory: MemoryStore,
        journals: JournalStore,
        tools: ToolRegistry,
        policy: PolicyEngine,
        llm: LLMRouter,
    ) -> None:
        self.settings = settings
        self.db = db
        self.memory = memory
        self.journals = journals
        self.tools = tools
        self.policy = policy
        self.llm = llm
        self.context_builder = ContextBuilder(settings=settings, memory=memory, tools=tools)
        self.redacted_log_path = settings.db_path.parent / "redacted.log"
        self._send_whatsapp = None
        self._send_cli = None

    def bind_channels(self, send_whatsapp, send_cli) -> None:
        self._send_whatsapp = send_whatsapp
        self._send_cli = send_cli

    def _redact(self, text: str) -> str:
        out = text
        for pattern in self.settings.redaction_patterns:
            out = re.sub(pattern, "[REDACTED]", out)
        return out

    def _write_redacted_log(self, event: str, payload: dict) -> None:
        self.redacted_log_path.parent.mkdir(parents=True, exist_ok=True)
        safe_payload = json.dumps(payload, ensure_ascii=True)
        safe_line = f"{datetime.now(timezone.utc).isoformat()} event={event} payload={self._redact(safe_payload)}\n"
        with self.redacted_log_path.open("a", encoding="utf-8") as fp:
            fp.write(safe_line)

    async def _send_text(self, inbound: InboundMessage, text: str) -> None:
        out = OutboundMessage(
            id=str(uuid4()),
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            text=text,
            reply_to=inbound.id,
        )
        await self._send(out)

    async def _send(self, message: OutboundMessage) -> None:
        if message.channel == "whatsapp" and self._send_whatsapp:
            await self._send_whatsapp(message)
            self.db.insert_ledger(message.id, "outbound", message.chat_id)
        elif message.channel == "cli" and self._send_cli:
            await self._send_cli(message.text or "")
        self._write_redacted_log(
            "outbound.message",
            {"message_id": message.id, "channel": message.channel, "chat_id": message.chat_id, "text": message.text or ""},
        )

        self.db.insert_message(
            message_id=message.id,
            channel=message.channel,
            chat_id=message.chat_id,
            sender_id="assistant",
            role="assistant",
            text=message.text,
            trace_id=str(uuid4()),
        )
        if message.text:
            self.memory.append_turn(message.chat_id, "assistant", message.text)

    def register_outbound_provider_id(self, provider_message_id: str, chat_id: str) -> None:
        """Store provider IDs so inbound echoes are suppressed by WhatsApp message id."""
        if provider_message_id:
            self.db.insert_ledger(provider_message_id, "outbound", chat_id)

    def _parse_tool_command(self, text: str) -> dict | None:
        text = text.strip()
        if text.startswith("/tool "):
            parts = text.split(" ", 2)
            if len(parts) < 3:
                return None
            tool_name = parts[1]
            try:
                args = json.loads(parts[2])
            except json.JSONDecodeError:
                return {"type": "response", "text": "Invalid JSON. Use /tool <name> <json>."}
            return {"type": "tool", "tool": tool_name, "args": args}

        if text.startswith("/schedule "):
            payload = text.removeprefix("/schedule ").strip()
            if "|" not in payload:
                return {
                    "type": "response",
                    "text": "Use /schedule <when> | <text>. Example: /schedule every monday at 9am | Weekly check-in",
                }
            when, reminder = [part.strip() for part in payload.split("|", 1)]
            return {
                "type": "tool",
                "tool": "scheduler",
                "args": {"action": "schedule", "when": when, "text": reminder},
            }

        if text.startswith("/jobs"):
            return {"type": "tool", "tool": "scheduler", "args": {"action": "list"}}

        return None

    async def _invoke_tool(
        self,
        inbound: InboundMessage,
        tool_name: str,
        args: dict[str, Any],
        *,
        confirmed: bool = False,
    ) -> ToolResult:
        call_args = {**args}
        call_args.setdefault("chat_id", inbound.chat_id)
        if confirmed:
            call_args["confirmed"] = True
        return await self.tools.execute(tool_name, call_args)

    async def _request_confirmation(
        self,
        inbound: InboundMessage,
        *,
        tool_name: str,
        risk_level: str,
        args: dict[str, Any],
    ) -> None:
        pending = self.policy.create_pending_action(
            chat_id=inbound.chat_id,
            tool_name=tool_name,
            risk_level=risk_level,
            proposed_args={"tool": tool_name, "args": args},
        )
        await self._send_text(
            inbound,
            (
                f"Confirmation required for {tool_name} ({risk_level}). "
                f"Reply YES to proceed or NO to cancel. Action ID: {pending.action_id}"
            ),
        )

    def _format_observation(self, result: ToolResult) -> str:
        content = self._redact((result.content or "").strip())
        if not content:
            content = "(no textual output)"
        limit = max(200, self.settings.agent_observation_max_chars)
        if len(content) > limit:
            content = f"{content[:limit]}...(truncated)"
        status = "ok" if result.ok else "error"
        return f"status={status}\ncontent={content}"

    async def _execute_tool(
        self,
        inbound: InboundMessage,
        tool_name: str,
        args: dict[str, Any],
        trace_id: str,
        confirmed: bool = False,
    ) -> None:
        result = await self._invoke_tool(inbound, tool_name, args, confirmed=confirmed)
        if result.requires_confirmation:
            await self._request_confirmation(
                inbound,
                tool_name=tool_name,
                risk_level=result.risk_level,
                args={**args, "chat_id": inbound.chat_id},
            )
            return

        safe_content = self._redact(result.content or "").strip()
        if not safe_content:
            safe_content = "Task completed, but there was no textual output."
        await self._send_text(inbound, safe_content)
        self.db.insert_audit(trace_id=trace_id, event="tool.execute", payload={"tool": tool_name, "ok": result.ok})
        self.journals.append_event(f"tool={tool_name} ok={result.ok} chat_id={inbound.chat_id}")

    async def _llm_step_decision(
        self,
        *,
        chat_id: str,
        user_text: str,
        step_messages: list[dict[str, str]],
        complex_task: bool,
    ) -> tuple[AgentDecision | None, str | None, str]:
        messages = self.context_builder.build_messages(
            chat_id=chat_id,
            user_text=user_text,
            step_messages=step_messages,
        )
        result = await self.llm.complete_json(messages=messages, complex_task=complex_task)
        if not result.get("ok"):
            return None, f"model routing failed: {result.get('error', 'unknown error')}", ""

        raw_content = result.get("content", "")
        try:
            decision = parse_agent_decision(raw_content)
            return decision, None, str(raw_content)
        except DecisionParseError as exc:
            return None, str(exc), str(raw_content)

    async def _run_react_loop(self, inbound: InboundMessage, trace_id: str) -> None:
        user_text = inbound.text or ""
        step_messages: list[dict[str, str]] = []
        complex_task = bool(
            user_text and any(token in user_text.lower() for token in ["research", "analyze", "complex", "compare", "plan"])
        )

        for step in range(1, max(1, self.settings.agent_max_steps) + 1):
            decision, error, raw_content = await self._llm_step_decision(
                chat_id=inbound.chat_id,
                user_text=user_text,
                step_messages=step_messages,
                complex_task=complex_task,
            )
            if error:
                self.db.insert_audit(
                    trace_id=trace_id,
                    event="loop.step",
                    payload={"step": step, "ok": False, "error": error},
                )
                correction = (
                    "Invalid decision output. "
                    "Return JSON object with required fields: thought + exactly one of call/response. "
                    f"Validation error: {error}"
                )
                snippet = raw_content.strip()
                if snippet:
                    step_messages.append({"role": "assistant", "content": snippet[:2000]})
                step_messages.append({"role": "user", "content": correction})
                continue

            assert decision is not None
            if decision.response is not None:
                self.db.insert_audit(
                    trace_id=trace_id,
                    event="loop.step",
                    payload={"step": step, "ok": True, "action": "response"},
                )
                await self._send_text(inbound, self._redact(decision.response))
                self.journals.append_event(f"response chat_id={inbound.chat_id}")
                return

            call = decision.call
            assert call is not None
            tool_name = call.name
            tool_args = call.arguments
            self.db.insert_audit(
                trace_id=trace_id,
                event="loop.step",
                payload={"step": step, "ok": True, "action": "call", "tool": tool_name},
            )

            result = await self._invoke_tool(inbound, tool_name, tool_args)
            if result.requires_confirmation:
                await self._request_confirmation(
                    inbound,
                    tool_name=tool_name,
                    risk_level=result.risk_level,
                    args={**tool_args, "chat_id": inbound.chat_id},
                )
                return

            self.db.insert_audit(
                trace_id=trace_id,
                event="tool.execute",
                payload={"tool": tool_name, "ok": result.ok},
            )
            observation = self._format_observation(result)
            self.db.insert_audit(
                trace_id=trace_id,
                event="loop.tool_observation",
                payload={"step": step, "tool": tool_name, "ok": result.ok},
            )
            self.journals.append_event(f"tool={tool_name} ok={result.ok} chat_id={inbound.chat_id}")

            step_messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "thought": decision.thought,
                            "call": {"name": tool_name, "arguments": tool_args},
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            step_messages.append({"role": "user", "content": f"TOOL_OBSERVATION:\n{observation}"})

        self.db.insert_audit(
            trace_id=trace_id,
            event="loop.max_steps_reached",
            payload={"max_steps": max(1, self.settings.agent_max_steps)},
        )
        await self._send_text(
            inbound,
            "I reached the maximum reasoning steps for this request. Please narrow the task or ask me to continue from a specific point.",
        )

    async def handle_inbound(self, inbound: InboundMessage, trace_id: str) -> None:
        if inbound.channel == "whatsapp" and not inbound.is_self_chat:
            logger.info(
                "Ignored WA message id=%s chat_id=%s because not self-chat",
                inbound.id,
                inbound.chat_id,
            )
            return
        if inbound.channel == "whatsapp" and not inbound.is_from_me:
            logger.info(
                "Ignored WA message id=%s chat_id=%s because not from-me",
                inbound.id,
                inbound.chat_id,
            )
            return

        claimed = self.db.claim_ledger(inbound.id, "inbound", inbound.chat_id)
        if not claimed:
            reason = "it is already present in the inbound ledger"
            if inbound.channel == "whatsapp" and self.db.ledger_contains(inbound.id, direction="outbound"):
                reason = "it matches outbound ledger"
            logger.info(
                "Ignored WA message id=%s chat_id=%s because %s",
                inbound.id,
                inbound.chat_id,
                reason,
            )
            return

        text = inbound.text or ""
        if inbound.channel == "whatsapp" and not text.strip() and not inbound.media:
            logger.info(
                "Ignored WA message id=%s chat_id=%s because it has no text/media payload",
                inbound.id,
                inbound.chat_id,
            )
            return

        self.db.insert_message(
            message_id=inbound.id,
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            sender_id=inbound.sender_id,
            role="user",
            text=self._redact(text),
            trace_id=trace_id,
        )
        self._write_redacted_log(
            "inbound.message",
            {
                "message_id": inbound.id,
                "channel": inbound.channel,
                "chat_id": inbound.chat_id,
                "sender_id": inbound.sender_id,
                "text": text,
            },
        )
        self.memory.append_turn(inbound.chat_id, "user", text)

        if text.strip():
            maybe_pending = self.policy.resolve_pending_action_from_text(inbound.chat_id, text)
            if maybe_pending:
                if maybe_pending.status == "approved":
                    proposed = maybe_pending.proposed_args
                    await self._execute_tool(
                        inbound,
                        tool_name=proposed["tool"],
                        args=proposed["args"],
                        trace_id=trace_id,
                        confirmed=True,
                    )
                else:
                    await self._send_text(inbound, "Cancelled pending action.")
                return

        direct = self._parse_tool_command(text)
        if direct:
            if direct.get("type") == "tool":
                tool_name = direct.get("tool", "")
                args = direct.get("args", {}) if isinstance(direct.get("args"), dict) else {}
                logger.info("Executing direct tool=%s for chat_id=%s", tool_name, inbound.chat_id)
                await self._execute_tool(inbound, tool_name=tool_name, args=args, trace_id=trace_id)
                return
            if direct.get("type") == "response":
                response = str(direct.get("text") or "")
                await self._send_text(inbound, self._redact(response))
                self.journals.append_event(f"response chat_id={inbound.chat_id}")
                return

        await self._run_react_loop(inbound, trace_id=trace_id)

    async def emit_scheduler_message(self, chat_id: str, text: str) -> None:
        channel = "cli" if chat_id == "cli-user" else "whatsapp"
        out = OutboundMessage(
            id=str(uuid4()),
            channel=channel,
            chat_id=chat_id,
            text=f"[Reminder] {text}",
        )
        await self._send(out)
