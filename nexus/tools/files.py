from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from nexus.tools.base import BaseTool, ToolResult, ToolSpec


class FileSystemTool(BaseTool):
    name = "filesystem"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Sandboxed file operations in workspace",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read_file", "write_file", "list_dir", "grep_search", "delete_file"],
                    },
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "pattern": {"type": "string"},
                },
                "required": ["action"],
            },
        )

    def _resolve_path(self, path_str: str) -> Path:
        target = (self.workspace / path_str).resolve() if not Path(path_str).is_absolute() else Path(path_str).resolve()
        if self.workspace not in target.parents and target != self.workspace:
            raise PermissionError("Path escapes sandbox workspace")
        return target

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = args.get("action")
        try:
            if action == "read_file":
                path = self._resolve_path(args["path"])
                if not path.exists():
                    return ToolResult(ok=False, content=f"File not found: {path}")
                return ToolResult(ok=True, content=path.read_text(encoding="utf-8"))

            if action == "write_file":
                path = self._resolve_path(args["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                exists = path.exists()
                if exists and not args.get("confirmed"):
                    return ToolResult(
                        ok=False,
                        content=f"Overwrite requires confirmation for {path}",
                        requires_confirmation=True,
                        risk_level="high",
                        proposed_action={"action": action, **args},
                    )
                path.write_text(args.get("content", ""), encoding="utf-8")
                return ToolResult(ok=True, content=f"Wrote {path}")

            if action == "list_dir":
                rel = args.get("path", ".")
                path = self._resolve_path(rel)
                if not path.exists():
                    return ToolResult(ok=False, content=f"Directory not found: {path}")
                entries = []
                for item in sorted(path.iterdir()):
                    suffix = "/" if item.is_dir() else ""
                    entries.append(str(item.relative_to(self.workspace)) + suffix)
                if not entries:
                    return ToolResult(ok=True, content=f"Directory is empty: {path}")
                return ToolResult(ok=True, content="\n".join(entries))

            if action == "grep_search":
                pattern = args.get("pattern")
                if not pattern:
                    return ToolResult(ok=False, content="pattern is required")
                rel = args.get("path", ".")
                root = self._resolve_path(rel)
                found = []
                regex = re.compile(pattern)
                for file_path in root.rglob("*"):
                    if not file_path.is_file():
                        continue
                    try:
                        for i, line in enumerate(
                            file_path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
                        ):
                            if regex.search(line):
                                found.append(f"{file_path.relative_to(self.workspace)}:{i}:{line.strip()}")
                    except OSError:
                        continue
                return ToolResult(ok=True, content="\n".join(found[:200]) or "No matches found")

            if action == "delete_file":
                path = self._resolve_path(args["path"])
                if not path.exists():
                    return ToolResult(ok=False, content=f"File not found: {path}")
                if not args.get("confirmed"):
                    return ToolResult(
                        ok=False,
                        content=f"Delete requires confirmation for {path}",
                        requires_confirmation=True,
                        risk_level="high",
                        proposed_action={"action": action, **args},
                    )
                path.unlink()
                return ToolResult(ok=True, content=f"Deleted {path}")
        except PermissionError as exc:
            return ToolResult(ok=False, content=f"Permission denied: {exc}")

        return ToolResult(ok=False, content=f"Unsupported action: {action}")
