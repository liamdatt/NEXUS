from __future__ import annotations

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


class EmailTool(BaseTool):
    name = "email"

    def __init__(self, settings: Settings, client: GmailClient | None = None) -> None:
        self.settings = settings
        self.client = client or GmailClient(settings)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=(
                "Read and summarize inbox emails, search messages, and draft/send emails. "
                "Sending requires explicit confirmation."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["summarize_unread", "summarize_search", "send_email"]},
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "to": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                    "cc": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                    "bcc": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                    "subject": {"type": "string"},
                    "body_text": {"type": "string"},
                    "body_html": {"type": "string"},
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
            lines.append(f"{i}. {subject}\n   From: {sender}\n   Date: {date}\n   Summary: {snippet}")
        return "\n".join(lines)

    @staticmethod
    def _draft_preview(
        to: list[str],
        cc: list[str],
        bcc: list[str],
        subject: str,
        body_text: str,
        body_html: str,
    ) -> str:
        preview_lines = [
            "Draft email ready for confirmation.",
            f"To: {', '.join(to) if to else '(none)'}",
            f"Cc: {', '.join(cc) if cc else '(none)'}",
            f"Bcc: {', '.join(bcc) if bcc else '(none)'}",
            f"Subject: {subject or '(no subject)'}",
        ]
        if body_text.strip():
            preview_lines.append(f"Body (text): {body_text.strip()[:500]}")
        elif body_html.strip():
            preview_lines.append(f"Body (html): {body_html.strip()[:500]}")
        else:
            preview_lines.append("Body: (empty)")
        preview_lines.append("Reply YES to send or NO to cancel.")
        return "\n".join(preview_lines)

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = args.get("action")
        try:
            max_results = int(args.get("max_results") or self.settings.email_summary_max_results or 10)
        except (TypeError, ValueError):
            return ToolResult(ok=False, content="max_results must be an integer")
        max_results = max(1, min(max_results, 25))

        if action == "summarize_unread":
            try:
                messages = self.client.list_messages("is:unread", max_results=max_results)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"email unread summary failed: {exc}")
            return ToolResult(ok=True, content=self._format_summary(messages))

        if action == "summarize_search":
            query = str(args.get("query") or "").strip()
            if not query:
                return ToolResult(ok=False, content="query is required for summarize_search")
            try:
                messages = self.client.list_messages(query, max_results=max_results)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"email search summary failed: {exc}")
            return ToolResult(ok=True, content=self._format_summary(messages))

        if action == "send_email":
            to = _to_email_list(args.get("to"))
            cc = _to_email_list(args.get("cc"))
            bcc = _to_email_list(args.get("bcc"))
            subject = str(args.get("subject") or "").strip()
            body_text = str(args.get("body_text") or "")
            body_html = str(args.get("body_html") or "")

            if not to:
                return ToolResult(ok=False, content="to recipient(s) are required for send_email")

            preview = self._draft_preview(to, cc, bcc, subject, body_text, body_html)
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=preview,
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )

            try:
                result = self.client.send_message(
                    to=to,
                    cc=cc,
                    bcc=bcc,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"send_email failed: {exc}")

            return ToolResult(
                ok=True,
                content=(
                    "Email sent successfully.\n"
                    f"id={result.get('id')}\n"
                    f"thread_id={result.get('thread_id')}"
                ),
            )

        return ToolResult(ok=False, content=f"Unsupported action: {action}")
