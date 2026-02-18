from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
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


def _normalize_wa_identity(value: str) -> str:
    raw = value.strip().lower()
    if not raw:
        return ""
    if "@" not in raw:
        return raw.split(":", 1)[0]
    user, domain = raw.split("@", 1)
    user = user.split(":", 1)[0]
    return f"{user}@{domain}" if user and domain else ""


def _wa_user(value: str) -> str:
    normalized = _normalize_wa_identity(value)
    if not normalized:
        return ""
    if "@" not in normalized:
        return normalized
    return normalized.split("@", 1)[0]


def _wa_sender_matches_chat(sender_id: str, chat_id: str) -> bool:
    sender_user = _wa_user(sender_id)
    chat_user = _wa_user(chat_id)
    return bool(sender_user and chat_user and sender_user == chat_user)


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
        try:
            safe_payload = json.dumps(payload, ensure_ascii=True)
        except Exception as exc:  # noqa: BLE001
            safe_payload = json.dumps(
                {
                    "serialization_error": str(exc),
                    "payload_repr": repr(payload),
                },
                ensure_ascii=True,
            )
        safe_line = f"{datetime.now(timezone.utc).isoformat()} event={event} payload={self._redact(safe_payload)}\n"
        with self.redacted_log_path.open("a", encoding="utf-8") as fp:
            fp.write(safe_line)

    @staticmethod
    def _media_line(media: Any) -> str:
        if not isinstance(media, dict):
            return "- unknown media payload"
        media_type = str(media.get("type") or "unknown")
        file_name = str(media.get("file_name") or "(unnamed)")
        mime_type = str(media.get("mime_type") or "-")
        local_path = str(media.get("local_path") or "-")
        size_bytes = media.get("size_bytes")
        status = str(media.get("download_status") or "unknown")
        error = str(media.get("download_error") or "")
        size_text = str(size_bytes) if isinstance(size_bytes, int) else "-"
        line = (
            f"- type={media_type} file_name={file_name} mime={mime_type} "
            f"local_path={local_path} size_bytes={size_text} status={status}"
        )
        if error:
            line += f" error={error}"
        return line

    def _render_media_context_block(self, media_items: list[dict[str, Any]] | None) -> str:
        if not media_items:
            return ""
        lines = ["[MEDIA_CONTEXT]"]
        lines.extend(self._media_line(item) for item in media_items)
        lines.append("[/MEDIA_CONTEXT]")
        return "\n".join(lines)

    def _effective_user_text(self, inbound: InboundMessage) -> str:
        text = (inbound.text or "").strip()
        media_items = [item.model_dump() for item in (inbound.media or [])]
        media_block = self._render_media_context_block(media_items)
        if text and media_block:
            return f"{text}\n\n{media_block}"
        if media_block:
            return media_block
        return text

    def _attachments_from_artifacts(self, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                continue
            file_path = Path(raw_path).expanduser().resolve()
            if not file_path.exists() or not file_path.is_file():
                continue
            mime_type = str(item.get("mime_type") or "").strip()
            raw_type = str(item.get("type") or "").strip().lower()
            if raw_type not in {"image", "document"}:
                if mime_type.startswith("image/"):
                    raw_type = "image"
                else:
                    raw_type = "document"
            payload: dict[str, Any] = {
                "type": raw_type,
                "path": str(file_path),
                "file_name": str(item.get("file_name") or file_path.name),
            }
            if mime_type:
                payload["mime_type"] = mime_type
            caption = str(item.get("caption") or "").strip()
            if caption:
                payload["caption"] = caption
            out.append(payload)
        return out

    async def _send_tool_result(self, inbound: InboundMessage, result: ToolResult) -> None:
        safe_content = self._redact(result.content or "").strip()
        if not safe_content:
            safe_content = "Task completed, but there was no textual output."

        attachments = self._attachments_from_artifacts(result.artifacts)
        if inbound.channel == "cli" and attachments:
            attachment_lines = "\n".join(
                f"- {att.get('file_name') or att.get('path')}: {att.get('path')}" for att in attachments
            )
            safe_content = f"{safe_content}\n\nGenerated files:\n{attachment_lines}"
            attachments = []

        out = OutboundMessage(
            id=str(uuid4()),
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            text=safe_content,
            attachments=attachments or None,
            reply_to=inbound.id,
        )
        await self._send(out)

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
            {
                "message_id": message.id,
                "channel": message.channel,
                "chat_id": message.chat_id,
                "text": message.text or "",
                "attachments": [att.model_dump() for att in (message.attachments or [])],
            },
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
        if result.artifacts:
            artifact_lines: list[str] = []
            for artifact in result.artifacts:
                if not isinstance(artifact, dict):
                    continue
                artifact_lines.append(
                    f"- type={artifact.get('type', '-')}, path={artifact.get('path', '-')}, file_name={artifact.get('file_name', '-')}"
                )
            if artifact_lines:
                return (
                    f"status={status}\ncontent={content}\nartifacts_count={len(artifact_lines)}\nartifacts=\n"
                    + "\n".join(artifact_lines)
                )
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

        await self._send_tool_result(inbound, result)
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
            if result.artifacts:
                await self._send_tool_result(inbound, result)
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
        try:
            if inbound.channel == "whatsapp":
                if not inbound.is_self_chat:
                    logger.info(
                        "Ignored WA message id=%s chat_id=%s because not self-chat",
                        inbound.id,
                        inbound.chat_id,
                    )
                    return
                if not inbound.is_from_me:
                    if _wa_sender_matches_chat(inbound.sender_id, inbound.chat_id):
                        logger.info(
                            "Accepted WA message id=%s chat_id=%s despite from-me=false because sender_id matches chat identity",
                            inbound.id,
                            inbound.chat_id,
                        )
                    else:
                        logger.info(
                            "Ignored WA message id=%s chat_id=%s because not from-me and sender_id does not match chat identity",
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

            raw_text = inbound.text or ""
            effective_text = self._effective_user_text(inbound)
            if inbound.channel == "whatsapp" and not raw_text.strip() and not inbound.media:
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
                text=self._redact(effective_text),
                trace_id=trace_id,
            )
            self._write_redacted_log(
                "inbound.message",
                {
                    "message_id": inbound.id,
                    "channel": inbound.channel,
                    "chat_id": inbound.chat_id,
                    "sender_id": inbound.sender_id,
                    "text": raw_text,
                    "media": [item.model_dump() for item in (inbound.media or [])],
                },
            )
            self.memory.append_turn(inbound.chat_id, "user", effective_text)

            if raw_text.strip():
                maybe_pending = self.policy.resolve_pending_action_from_text(inbound.chat_id, raw_text)
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

            direct = self._parse_tool_command(raw_text)
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

            llm_inbound = inbound.model_copy(update={"text": effective_text})
            await self._run_react_loop(llm_inbound, trace_id=trace_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Inbound processing failed id=%s chat_id=%s trace_id=%s",
                inbound.id,
                inbound.chat_id,
                trace_id,
            )
            try:
                self.db.insert_audit(
                    trace_id=trace_id,
                    event="inbound.error",
                    payload={
                        "message_id": inbound.id,
                        "chat_id": inbound.chat_id,
                        "channel": inbound.channel,
                        "error": str(exc),
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to persist inbound.error audit id=%s chat_id=%s trace_id=%s",
                    inbound.id,
                    inbound.chat_id,
                    trace_id,
                )

            fallback = "I hit an internal processing error while handling that request. Please try again."
            try:
                await self._send_text(inbound, fallback)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to send inbound error reply id=%s chat_id=%s trace_id=%s",
                    inbound.id,
                    inbound.chat_id,
                    trace_id,
                )

    async def emit_scheduler_message(self, chat_id: str, text: str) -> None:
        channel = "cli" if chat_id == "cli-user" else "whatsapp"
        out = OutboundMessage(
            id=str(uuid4()),
            channel=channel,
            chat_id=chat_id,
            text=f"[Reminder] {text}",
        )
        await self._send(out)
