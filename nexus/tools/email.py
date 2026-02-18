from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus.config import Settings
from nexus.integrations.gmail_client import GmailClient
from nexus.tools.base import BaseTool, ToolResult, ToolSpec


def _to_email_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [chunk.strip() for chunk in value.replace(";", ",").split(",")]
        return [part for part in parts if part]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    return []


def _to_attachment_candidates(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    out: list[dict[str, str]] = []
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            out.append({"path": cleaned})
        return out
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                cleaned = item.strip()
                if cleaned:
                    out.append({"path": cleaned})
                continue
            if isinstance(item, dict):
                raw_path = str(item.get("path") or "").strip()
                if not raw_path:
                    continue
                normalized: dict[str, str] = {"path": raw_path}
                file_name = str(item.get("file_name") or "").strip()
                mime_type = str(item.get("mime_type") or "").strip()
                if file_name:
                    normalized["file_name"] = file_name
                if mime_type:
                    normalized["mime_type"] = mime_type
                out.append(normalized)
    return out


class EmailTool(BaseTool):
    name = "email"

    def __init__(self, settings: Settings, client: GmailClient | None = None) -> None:
        self.settings = settings
        self.client = client or GmailClient(settings)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=(
                "Read Gmail threads/messages and perform draft/send/reply operations. "
                "Write actions require explicit confirmation."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "summarize_unread",
                            "summarize_search",
                            "search_threads",
                            "search_messages",
                            "send_email",
                            "create_draft",
                            "send_draft",
                            "reply",
                        ],
                    },
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "to": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                    "cc": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                    "bcc": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                    "subject": {"type": "string"},
                    "body_text": {"type": "string"},
                    "body_html": {"type": "string"},
                    "draft_id": {"type": "string"},
                    "reply_to_message_id": {"type": "string"},
                    "thread_id": {"type": "string"},
                    "attachments": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array"},
                        ]
                    },
                },
                "required": ["action"],
            },
        )

    @staticmethod
    def _format_summary(messages: list[dict[str, Any]]) -> str:
        if not messages:
            return "No matching emails found."
        lines = []
        for i, msg in enumerate(messages, start=1):
            sender = msg.get("from") or "(unknown sender)"
            subject = msg.get("subject") or "(no subject)"
            date = msg.get("date") or "(no date)"
            snippet = (msg.get("snippet") or "").strip()
            snippet = snippet.replace("\n", " ").strip()
            if len(snippet) > 220:
                snippet = snippet[:217] + "..."
            thread_id = msg.get("thread_id") or "-"
            message_count = msg.get("message_count")
            line = (
                f"{i}. {subject}\n"
                f"   From: {sender}\n"
                f"   Date: {date}\n"
                f"   Thread: {thread_id}"
            )
            if message_count is not None:
                line += f"\n   Messages: {message_count}"
            line += f"\n   Summary: {snippet}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _write_preview(
        *,
        action: str,
        to: list[str],
        cc: list[str],
        bcc: list[str],
        subject: str,
        body_text: str,
        body_html: str,
        draft_id: str = "",
        reply_to_message_id: str = "",
        attachments: list[dict[str, str]] | None = None,
    ) -> str:
        preview_lines = [
            f"Email action requires confirmation: {action}",
            f"To: {', '.join(to) if to else '(none)'}",
            f"Cc: {', '.join(cc) if cc else '(none)'}",
            f"Bcc: {', '.join(bcc) if bcc else '(none)'}",
            f"Subject: {subject or '(no subject)'}",
        ]
        if draft_id:
            preview_lines.append(f"Draft ID: {draft_id}")
        if reply_to_message_id:
            preview_lines.append(f"Reply-To-Message-ID: {reply_to_message_id}")
        if body_text.strip():
            preview_lines.append(f"Body (text): {body_text.strip()[:500]}")
        elif body_html.strip():
            preview_lines.append(f"Body (html): {body_html.strip()[:500]}")
        if attachments:
            preview_lines.append(f"Attachments: {', '.join(item.get('file_name') or item.get('path', '-') for item in attachments)}")
        preview_lines.append("Reply YES to proceed or NO to cancel.")
        return "\n".join(preview_lines)

    def _normalize_attachments(self, raw_value: Any) -> tuple[list[dict[str, str]], str | None]:
        attachments = _to_attachment_candidates(raw_value)
        if not attachments:
            return [], None
        workspace = self.settings.workspace.resolve()
        resolved: list[dict[str, str]] = []
        for item in attachments:
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                continue
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = workspace / candidate
            full = candidate.resolve()
            if workspace != full and workspace not in full.parents:
                return [], f"attachment path escapes workspace: {full}"
            if not full.exists() or not full.is_file():
                return [], f"attachment file not found: {full}"
            payload = {
                "path": str(full),
                "file_name": str(item.get("file_name") or full.name),
            }
            mime_type = str(item.get("mime_type") or "").strip()
            if mime_type:
                payload["mime_type"] = mime_type
            resolved.append(payload)
        return resolved, None

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = str(args.get("action") or "")
        try:
            max_results = int(args.get("max_results") or self.settings.email_summary_max_results or 10)
        except (TypeError, ValueError):
            return ToolResult(ok=False, content="max_results must be an integer")
        max_results = max(1, min(max_results, 50))

        if action == "summarize_unread":
            try:
                messages = self.client.list_messages("is:unread", max_results=max_results)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"email unread summary failed: {exc}")
            return ToolResult(ok=True, content=self._format_summary(messages))

        if action in {"summarize_search", "search_messages"}:
            query = str(args.get("query") or "").strip()
            if not query:
                return ToolResult(ok=False, content=f"query is required for {action}")
            try:
                messages = self.client.list_messages(query, max_results=max_results)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"email message search failed: {exc}")
            return ToolResult(ok=True, content=self._format_summary(messages))

        if action == "search_threads":
            query = str(args.get("query") or "").strip()
            if not query:
                return ToolResult(ok=False, content="query is required for search_threads")
            try:
                threads = self.client.search_threads(query, max_results=max_results)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"email thread search failed: {exc}")
            return ToolResult(ok=True, content=self._format_summary(threads))

        to = _to_email_list(args.get("to"))
        cc = _to_email_list(args.get("cc"))
        bcc = _to_email_list(args.get("bcc"))
        subject = str(args.get("subject") or "").strip()
        body_text = str(args.get("body_text") or "")
        body_html = str(args.get("body_html") or "")
        draft_id = str(args.get("draft_id") or "").strip()
        reply_to_message_id = str(args.get("reply_to_message_id") or "").strip()
        thread_id = str(args.get("thread_id") or "").strip() or None
        attachments, attachment_error = self._normalize_attachments(args.get("attachments"))
        if attachment_error:
            return ToolResult(ok=False, content=attachment_error)

        if action == "send_draft":
            if not draft_id:
                return ToolResult(ok=False, content="draft_id is required for send_draft")
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._write_preview(
                        action=action,
                        to=[],
                        cc=[],
                        bcc=[],
                        subject="",
                        body_text="",
                        body_html="",
                        draft_id=draft_id,
                        attachments=attachments,
                    ),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )
            try:
                result = self.client.send_draft(draft_id=draft_id)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"send_draft failed: {exc}")
            return ToolResult(
                ok=True,
                content=f"Draft sent.\nid={result.get('id')}\nthread_id={result.get('thread_id')}",
            )

        if action in {"send_email", "create_draft", "reply"}:
            if not to:
                return ToolResult(ok=False, content=f"to recipient(s) are required for {action}")
            if action == "reply" and not reply_to_message_id:
                return ToolResult(ok=False, content="reply_to_message_id is required for reply")

            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._write_preview(
                        action=action,
                        to=to,
                        cc=cc,
                        bcc=bcc,
                        subject=subject,
                        body_text=body_text,
                        body_html=body_html,
                        reply_to_message_id=reply_to_message_id,
                        attachments=attachments,
                    ),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )

            try:
                if action == "create_draft":
                    result = self.client.create_draft(
                        to=to,
                        cc=cc,
                        bcc=bcc,
                        subject=subject,
                        body_text=body_text,
                        body_html=body_html,
                        reply_to_message_id=reply_to_message_id or None,
                        thread_id=thread_id,
                        attachments=attachments,
                    )
                    return ToolResult(
                        ok=True,
                        content=(
                            "Draft created.\n"
                            f"id={result.get('id')}\n"
                            f"message_id={result.get('message_id')}\n"
                            f"thread_id={result.get('thread_id')}"
                        ),
                    )

                result = self.client.send_message(
                    to=to,
                    cc=cc,
                    bcc=bcc,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    reply_to_message_id=reply_to_message_id or None,
                    thread_id=thread_id,
                    attachments=attachments,
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"{action} failed: {exc}")

            success_label = "Reply sent successfully." if action == "reply" else "Email sent successfully."
            return ToolResult(
                ok=True,
                content=(
                    f"{success_label}\n"
                    f"id={result.get('id')}\n"
                    f"thread_id={result.get('thread_id')}"
                ),
            )

        return ToolResult(ok=False, content=f"Unsupported action: {action}")
