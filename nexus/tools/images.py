from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus.config import Settings
from nexus.integrations.openrouter_images import OpenRouterImageClient
from nexus.tools.base import BaseTool, ToolResult, ToolSpec

DEFAULT_IMAGE_MODEL = "google/gemini-2.5-flash-image"


class ImagesTool(BaseTool):
    name = "images"

    def __init__(self, settings: Settings, client: OpenRouterImageClient | None = None) -> None:
        self.settings = settings
        self.client = client or OpenRouterImageClient(settings)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Generate and edit images using OpenRouter image-capable models.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["generate", "edit"]},
                    "prompt": {"type": "string"},
                    "model": {"type": "string"},
                    "size": {"type": "string"},
                    "resolution": {"type": "string", "enum": ["1K", "2K", "4K"]},
                    "output_path": {"type": "string"},
                    "input_paths": {
                        "oneOf": [
                            {"type": "array", "items": {"type": "string"}},
                            {"type": "string"},
                        ]
                    },
                    "confirmed": {"type": "boolean"},
                },
                "required": ["action", "prompt"],
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

    @staticmethod
    def _to_path_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            cleaned = value.strip()
            return [cleaned] if cleaned else []
        if isinstance(value, list):
            out: list[str] = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    out.append(item.strip())
            return out
        return []

    @staticmethod
    def _format_artifacts(artifacts: list[dict[str, str]]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for item in artifacts:
            out.append(
                {
                    "type": "image",
                    "path": str(item.get("path") or ""),
                    "file_name": str(item.get("file_name") or "image.png"),
                    "mime_type": str(item.get("mime_type") or "image/png"),
                }
            )
        return [item for item in out if item.get("path")]

    @staticmethod
    def _preview(
        action: str,
        model: str,
        prompt: str,
        input_count: int,
        size: str,
        resolution: str,
        output_path: str,
    ) -> str:
        return "\n".join(
            [
                "Image operation requires confirmation.",
                f"action={action}",
                f"model={model}",
                f"size={size or '-'}",
                f"resolution={resolution or '-'}",
                f"output_path={output_path or '-'}",
                f"input_images={input_count}",
                f"prompt={prompt[:280]}",
                "Reply YES to proceed or NO to cancel.",
            ]
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = str(args.get("action") or "")
        prompt = str(args.get("prompt") or "").strip()
        model = str(args.get("model") or DEFAULT_IMAGE_MODEL).strip() or DEFAULT_IMAGE_MODEL
        size = str(args.get("size") or "").strip()
        resolution = str(args.get("resolution") or "").strip()
        output_path = str(args.get("output_path") or "").strip()

        if action not in {"generate", "edit"}:
            return ToolResult(ok=False, content=f"Unsupported action: {action}")
        if not prompt:
            return ToolResult(ok=False, content="prompt is required")

        raw_inputs = self._to_path_list(args.get("input_paths"))
        resolved_inputs: list[str] = []
        if raw_inputs:
            try:
                resolved_inputs = [str(self._resolve_workspace_path(item)) for item in raw_inputs]
            except PermissionError as exc:
                return ToolResult(ok=False, content=f"input path rejected: {exc}")

        if output_path:
            try:
                _ = self._resolve_workspace_path(output_path)
            except PermissionError as exc:
                return ToolResult(ok=False, content=f"output path rejected: {exc}")

        if action == "edit" and not resolved_inputs:
            return ToolResult(ok=False, content="input_paths are required for edit")

        if not args.get("confirmed"):
            return ToolResult(
                ok=False,
                content=self._preview(
                    action,
                    model,
                    prompt,
                    len(resolved_inputs),
                    size,
                    resolution,
                    output_path,
                ),
                requires_confirmation=True,
                risk_level="high",
                proposed_action={"action": action, **args},
            )

        try:
            if action == "generate":
                data = self.client.generate(
                    prompt=prompt,
                    model=model,
                    size=size or None,
                    resolution=resolution or None,
                    output_path=output_path or None,
                )
            else:
                data = self.client.edit(
                    prompt=prompt,
                    input_paths=resolved_inputs,
                    model=model,
                    size=size or None,
                    resolution=resolution or None,
                    output_path=output_path or None,
                )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, content=f"image {action} failed: {exc}")

        artifacts = self._format_artifacts(data.get("artifacts") if isinstance(data, dict) else [])
        if not artifacts:
            return ToolResult(ok=False, content=f"image {action} failed: no images returned")

        summary = str(data.get("text") or "").strip()
        files = ", ".join(item.get("file_name") or "image" for item in artifacts)
        text = f"Image {action} complete. Files: {files}"
        if summary:
            text += f"\n\n{summary[:1200]}"

        return ToolResult(ok=True, content=text, artifacts=artifacts)
