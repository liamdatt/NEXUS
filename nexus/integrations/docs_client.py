from __future__ import annotations

from typing import Any

from nexus.config import Settings
from nexus.integrations.google_auth import load_google_credentials


class DocsClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _docs_service(self):
        try:
            from googleapiclient.discovery import build  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Google API client dependency missing. Reinstall project dependencies."
            ) from exc
        creds = load_google_credentials(self.settings)
        return build("docs", "v1", credentials=creds, cache_discovery=False)

    def _drive_service(self):
        try:
            from googleapiclient.discovery import build  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Google API client dependency missing. Reinstall project dependencies."
            ) from exc
        creds = load_google_credentials(self.settings)
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    @staticmethod
    def _extract_text(document: dict[str, Any]) -> str:
        parts: list[str] = []
        body = document.get("body", {}) if isinstance(document, dict) else {}
        content = body.get("content", []) if isinstance(body, dict) else []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            paragraph = block.get("paragraph")
            if not isinstance(paragraph, dict):
                continue
            elements = paragraph.get("elements", [])
            if not isinstance(elements, list):
                continue
            for element in elements:
                if not isinstance(element, dict):
                    continue
                text_run = element.get("textRun")
                if not isinstance(text_run, dict):
                    continue
                text = text_run.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()

    def cat_document(self, document_id: str) -> dict[str, Any]:
        service = self._docs_service()
        document = service.documents().get(documentId=document_id).execute()
        return {
            "document_id": str(document.get("documentId", "")),
            "title": str(document.get("title", "")),
            "text": self._extract_text(document),
        }

    def export_document(self, document_id: str, format_name: str) -> dict[str, Any]:
        format_map = {
            "txt": "text/plain",
            "html": "text/html",
        }
        fmt = format_name.strip().lower()
        mime_type = format_map.get(fmt)
        if not mime_type:
            raise RuntimeError("Unsupported export format. Use txt or html.")

        service = self._drive_service()
        payload = service.files().export(fileId=document_id, mimeType=mime_type).execute()
        if isinstance(payload, bytes):
            text = payload.decode("utf-8", errors="replace")
        else:
            text = str(payload)
        return {"document_id": document_id, "format": fmt, "content": text}
