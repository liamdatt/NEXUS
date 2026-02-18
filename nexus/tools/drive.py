from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus.config import Settings
from nexus.integrations.drive_client import DriveClient
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


class DriveTool(BaseTool):
    name = "drive"

    def __init__(self, settings: Settings, client: DriveClient | None = None) -> None:
        self.settings = settings
        self.client = client or DriveClient(settings)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Search and upload Google Drive files.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["search", "upload"]},
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "path": {"type": "string"},
                    "name": {"type": "string"},
                    "mime_type": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["action"],
            },
        )

    def _resolve_workspace_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.settings.workspace / candidate
        resolved = candidate.resolve()
        workspace = self.settings.workspace.resolve()
        if workspace != resolved and workspace not in resolved.parents:
            raise PermissionError("path escapes workspace")
        return resolved

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = str(args.get("action") or "")
        if action not in {"search", "upload"}:
            return ToolResult(ok=False, content=f"Unsupported action: {action}")

        if action == "upload":
            raw_path = str(args.get("path") or "").strip()
            if not raw_path:
                return ToolResult(ok=False, content="path is required for upload")
            try:
                source = self._resolve_workspace_path(raw_path)
            except PermissionError as exc:
                return ToolResult(ok=False, content=f"upload path rejected: {exc}")
            if not source.exists() or not source.is_file():
                return ToolResult(ok=False, content=f"file not found: {source}")
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=(
                        "Drive upload requires confirmation.\n"
                        f"path={source}\n"
                        f"name={str(args.get('name') or source.name)}\n"
                        "Reply YES to proceed or NO to cancel."
                    ),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )
            try:
                uploaded = self.client.upload_file(
                    source,
                    name=str(args.get("name") or "").strip() or source.name,
                    mime_type=str(args.get("mime_type") or "").strip() or None,
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=_normalize_google_error("drive upload failed", exc))
            return ToolResult(
                ok=True,
                content=(
                    "Drive upload complete.\n"
                    f"id={uploaded.get('id')}\n"
                    f"name={uploaded.get('name')}\n"
                    f"type={uploaded.get('mime_type')}\n"
                    f"link={uploaded.get('web_view_link') or uploaded.get('web_content_link') or '-'}"
                ),
            )

        query = str(args.get("query") or "").strip()
        try:
            max_results = int(args.get("max_results") or 10)
        except (TypeError, ValueError):
            return ToolResult(ok=False, content="max_results must be an integer")
        max_results = max(1, min(max_results, 50))

        try:
            rows = self.client.search(query=query, max_results=max_results)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, content=_normalize_google_error("drive search failed", exc))

        if not rows:
            return ToolResult(ok=True, content="No matching Drive files found.")
        lines = []
        for idx, row in enumerate(rows, start=1):
            lines.append(
                f"{idx}. {row.get('name') or '(untitled)'}\n"
                f"   id={row.get('id') or '-'}\n"
                f"   type={row.get('mime_type') or '-'}\n"
                f"   modified={row.get('modified_time') or '-'}\n"
                f"   link={row.get('web_view_link') or '-'}"
            )
        return ToolResult(ok=True, content="\n".join(lines))
