from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

from nexus.config import Settings


SIZE_TO_ASPECT_RATIO: dict[str, str] = {
    "1024x1024": "1:1",
    "832x1248": "2:3",
    "1248x832": "3:2",
    "864x1184": "3:4",
    "1184x864": "4:3",
    "896x1152": "4:5",
    "1152x896": "5:4",
    "768x1344": "9:16",
    "1344x768": "16:9",
    "1536x672": "21:9",
}

ALLOWED_RESOLUTIONS = {"1K", "2K", "4K"}


class OpenRouterImageClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _require_key(self) -> str:
        key = (self.settings.openrouter_api_key or "").strip()
        if not key:
            raise RuntimeError("OpenRouter API key is not configured. Set NEXUS_OPENROUTER_API_KEY.")
        return key

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = self._require_key()
        endpoint = f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        response = requests.post(endpoint, headers=headers, json=payload, timeout=120)
        if response.status_code >= 400:
            raise RuntimeError(f"OpenRouter request failed ({response.status_code}): {response.text}")
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("OpenRouter returned unexpected response shape")
        return body

    @staticmethod
    def _encode_path_data_url(source: Path) -> str:
        mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _extract_message(body: dict[str, Any]) -> dict[str, Any]:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("OpenRouter returned no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise RuntimeError("OpenRouter choice payload is invalid")
        message = first.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("OpenRouter choice message is missing")
        return message

    @staticmethod
    def _extract_text(message: dict[str, Any]) -> str:
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = str(item.get("text") or "").strip()
                    if text:
                        chunks.append(text)
            return "\n".join(chunks)
        return ""

    @staticmethod
    def _extract_image_urls(message: dict[str, Any]) -> list[str]:
        urls: list[str] = []

        images = message.get("images")
        if isinstance(images, list):
            for image in images:
                if not isinstance(image, dict):
                    continue
                image_url = image.get("image_url")
                if isinstance(image_url, dict):
                    url = str(image_url.get("url") or "").strip()
                    if url:
                        urls.append(url)

        content = message.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "image_url":
                    continue
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    url = str(image_url.get("url") or "").strip()
                    if url:
                        urls.append(url)

        deduped: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
        return deduped

    def _image_config(self, *, size: str | None, resolution: str | None) -> dict[str, str] | None:
        config: dict[str, str] = {}
        size_value = (size or "").strip()
        if size_value:
            aspect_ratio = SIZE_TO_ASPECT_RATIO.get(size_value)
            if not aspect_ratio:
                allowed = ", ".join(sorted(SIZE_TO_ASPECT_RATIO))
                raise RuntimeError(f"unsupported size '{size_value}'. Allowed: {allowed}")
            config["aspect_ratio"] = aspect_ratio

        resolution_value = (resolution or "").strip().upper()
        if resolution_value:
            if resolution_value not in ALLOWED_RESOLUTIONS:
                allowed_res = ", ".join(sorted(ALLOWED_RESOLUTIONS))
                raise RuntimeError(f"unsupported resolution '{resolution_value}'. Allowed: {allowed_res}")
            config["image_size"] = resolution_value

        return config or None

    def _resolve_output_target(self, output_path: str | None, mime_type: str, index: int, total: int) -> Path:
        suffix = ".png"
        if "jpeg" in mime_type or "jpg" in mime_type:
            suffix = ".jpg"
        elif "webp" in mime_type:
            suffix = ".webp"
        elif "gif" in mime_type:
            suffix = ".gif"

        if not output_path:
            output_dir = self.settings.workspace / "generated" / "images"
            output_dir.mkdir(parents=True, exist_ok=True)
            return output_dir / f"image-{uuid4().hex[:12]}{suffix}"

        candidate = Path(output_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.settings.workspace / candidate
        resolved = candidate.resolve()
        workspace = self.settings.workspace.resolve()
        if workspace != resolved and workspace not in resolved.parents:
            raise RuntimeError("output_path escapes workspace")

        if total <= 1:
            if resolved.suffix:
                return resolved
            return resolved.with_suffix(suffix)

        stem = resolved.stem or "image"
        numbered = resolved.with_name(f"{stem}-{index + 1}")
        if numbered.suffix:
            return numbered
        return numbered.with_suffix(suffix)

    def _save_data_url(self, data_url: str, output_path: str | None, index: int, total: int) -> dict[str, str]:
        if not data_url.startswith("data:") or ";base64," not in data_url:
            raise RuntimeError("image payload is not a base64 data URL")
        header, encoded = data_url.split(",", 1)
        mime_type = "image/png"
        if ";" in header:
            mime_type = header[5:].split(";", 1)[0] or mime_type

        raw = base64.b64decode(encoded)
        target = self._resolve_output_target(output_path, mime_type, index=index, total=total)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return {
            "path": str(target),
            "file_name": target.name,
            "mime_type": mime_type,
        }

    def generate(
        self,
        *,
        prompt: str,
        model: str,
        size: str | None = None,
        resolution: str | None = None,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
        }
        image_config = self._image_config(size=size, resolution=resolution)
        if image_config:
            payload["image_config"] = image_config

        body = self._post_chat(payload)
        message = self._extract_message(body)
        text = self._extract_text(message)
        image_urls = self._extract_image_urls(message)
        if not image_urls:
            raise RuntimeError("OpenRouter did not return generated images")

        artifacts = [
            self._save_data_url(url, output_path=output_path, index=idx, total=len(image_urls))
            for idx, url in enumerate(image_urls)
        ]
        return {
            "text": text,
            "artifacts": artifacts,
        }

    def edit(
        self,
        *,
        prompt: str,
        input_paths: list[str],
        model: str,
        size: str | None = None,
        resolution: str | None = None,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        if not input_paths:
            raise RuntimeError("input_paths is required for image edit")

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for raw in input_paths:
            source = Path(raw).expanduser().resolve()
            if not source.exists() or not source.is_file():
                raise RuntimeError(f"input image not found: {source}")
            data_url = self._encode_path_data_url(source)
            content.append({"type": "image_url", "image_url": {"url": data_url}})

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "modalities": ["image", "text"],
        }
        image_config = self._image_config(size=size, resolution=resolution)
        if image_config:
            payload["image_config"] = image_config

        body = self._post_chat(payload)
        message = self._extract_message(body)
        text = self._extract_text(message)
        image_urls = self._extract_image_urls(message)
        if not image_urls:
            raise RuntimeError("OpenRouter did not return edited images")

        artifacts = [
            self._save_data_url(url, output_path=output_path, index=idx, total=len(image_urls))
            for idx, url in enumerate(image_urls)
        ]
        return {
            "text": text,
            "artifacts": artifacts,
        }
