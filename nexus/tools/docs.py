from __future__ import annotations

from typing import Any

from nexus.config import Settings
from nexus.integrations.docs_client import DocsClient
from nexus.tools.base import BaseTool, ToolResult, ToolSpec


class DocsTool(BaseTool):
    name = "docs"

    def __init__(self, settings: Settings, client: DocsClient | None = None) -> None:
        self.settings = settings
        self.client = client or DocsClient(settings)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Read and export Google Docs content.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["cat", "export"]},
                    "document_id": {"type": "string"},
                    "format": {"type": "string"},
                },
                "required": ["action", "document_id"],
            },
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = str(args.get("action") or "")
        document_id = str(args.get("document_id") or "").strip()
        if not document_id:
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
                    f"Document export complete.\n"
                    f"id={data.get('document_id')}\n"
                    f"format={data.get('format')}\n\n"
                    f"{content}"
                ),
            )

        return ToolResult(ok=False, content=f"Unsupported action: {action}")
