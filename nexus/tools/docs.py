from __future__ import annotations

from typing import Any

from nexus.config import Settings
from nexus.integrations.docs_client import DocsClient
from nexus.tools.base import BaseTool, ToolResult, ToolSpec


SCOPE_GUIDANCE = (
    "Google connection is missing required scopes for this action. "
    "Disconnect and reconnect Google from the dashboard."
)


def _normalize_google_error(prefix: str, exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if (
        "insufficient authentication scopes" in lowered
        or "insufficientpermissions" in lowered
        or "insufficient permissions" in lowered
        or "insufficient permission" in lowered
    ):
        return f"{prefix}: {SCOPE_GUIDANCE}"
    return f"{prefix}: {message}"


class DocsTool(BaseTool):
    name = "docs"

    def __init__(self, settings: Settings, client: DocsClient | None = None) -> None:
        self.settings = settings
        self.client = client or DocsClient(settings)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=(
                "Read, create, edit, and export Google Docs content. "
                "Write actions require explicit confirmation."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["cat", "export", "create", "append_text", "replace_text"],
                    },
                    "document_id": {"type": "string"},
                    "format": {"type": "string"},
                    "title": {"type": "string"},
                    "initial_text": {"type": "string"},
                    "text": {"type": "string"},
                    "find_text": {"type": "string"},
                    "replace_text": {"type": "string"},
                    "match_case": {"type": "boolean"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["action"],
            },
        )

    @staticmethod
    def _write_confirmation_preview(action: str, details: list[str]) -> str:
        lines = [
            "Docs write operation requires confirmation.",
            f"action={action}",
            *details,
            "Reply YES to proceed or NO to cancel.",
        ]
        return "\n".join(lines)

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = str(args.get("action") or "").strip()

        if action == "create":
            title = str(args.get("title") or "").strip()
            initial_text = str(args.get("initial_text") or "")
            if not title:
                return ToolResult(ok=False, content="title is required for create")
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._write_confirmation_preview(
                        action,
                        [
                            f"title={title}",
                            f"initial_text_length={len(initial_text)}",
                        ],
                    ),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )
            try:
                data = self.client.create_document(title=title, initial_text=initial_text or None)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=_normalize_google_error("docs create failed", exc))
            return ToolResult(
                ok=True,
                content=(
                    "Document created.\n"
                    f"id={data.get('document_id')}\n"
                    f"title={data.get('title') or title}\n"
                    f"text_length={len(str(data.get('text') or ''))}"
                ),
            )

        document_id = str(args.get("document_id") or "").strip()
        if action in {"cat", "export", "append_text", "replace_text"} and not document_id:
            return ToolResult(ok=False, content="document_id is required")

        if action == "cat":
            try:
                data = self.client.cat_document(document_id=document_id)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"docs cat failed: {exc}")
            text = str(data.get("text") or "").strip()
            if len(text) > 8000:
                text = f"{text[:8000]}...(truncated)"
            return ToolResult(
                ok=True,
                content=(
                    f"Document: {data.get('title') or '(untitled)'}\n"
                    f"id={data.get('document_id') or document_id}\n\n"
                    f"{text or '(empty document)'}"
                ),
            )

        if action == "export":
            format_name = str(args.get("format") or "txt").strip().lower()
            try:
                data = self.client.export_document(document_id=document_id, format_name=format_name)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"docs export failed: {exc}")
            content = str(data.get("content") or "")
            if len(content) > 8000:
                content = f"{content[:8000]}...(truncated)"
            return ToolResult(
                ok=True,
                content=(
                    "Document export complete.\n"
                    f"id={data.get('document_id')}\n"
                    f"format={data.get('format')}\n\n"
                    f"{content}"
                ),
            )

        if action == "append_text":
            text = str(args.get("text") or "")
            if not text:
                return ToolResult(ok=False, content="text is required for append_text")
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._write_confirmation_preview(
                        action,
                        [
                            f"document_id={document_id}",
                            f"append_text_length={len(text)}",
                        ],
                    ),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )
            try:
                data = self.client.append_text(document_id=document_id, text=text)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=_normalize_google_error("docs append_text failed", exc))
            return ToolResult(
                ok=True,
                content=(
                    "Document text appended.\n"
                    f"id={data.get('document_id') or document_id}\n"
                    f"title={data.get('title') or '(untitled)'}\n"
                    f"appended_chars={data.get('appended_chars')}"
                ),
            )

        if action == "replace_text":
            find_text = str(args.get("find_text") or "")
            if not find_text:
                return ToolResult(ok=False, content="find_text is required for replace_text")
            if "replace_text" not in args:
                return ToolResult(ok=False, content="replace_text is required for replace_text")
            replacement_text = str(args.get("replace_text") or "")
            match_case = bool(args.get("match_case") or False)
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._write_confirmation_preview(
                        action,
                        [
                            f"document_id={document_id}",
                            f"find_text={find_text}",
                            f"replace_text_length={len(replacement_text)}",
                            f"match_case={match_case}",
                        ],
                    ),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )
            try:
                data = self.client.replace_text(
                    document_id=document_id,
                    find_text=find_text,
                    replace_text=replacement_text,
                    match_case=match_case,
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=_normalize_google_error("docs replace_text failed", exc))
            return ToolResult(
                ok=True,
                content=(
                    "Document text replaced.\n"
                    f"id={data.get('document_id') or document_id}\n"
                    f"title={data.get('title') or '(untitled)'}\n"
                    f"occurrences_changed={data.get('occurrences_changed', 0)}"
                ),
            )

        return ToolResult(ok=False, content=f"Unsupported action: {action}")
