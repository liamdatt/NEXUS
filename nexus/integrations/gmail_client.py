from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

from nexus.config import Settings
from nexus.integrations.google_auth import load_google_credentials


class GmailClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _service(self):
        try:
            from googleapiclient.discovery import build  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Google API client dependency missing. Reinstall project dependencies."
            ) from exc

        creds = load_google_credentials(self.settings)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    @staticmethod
    def _header_value(headers: list[dict[str, str]], name: str) -> str:
        lowered = name.lower()
        for header in headers:
            if str(header.get("name", "")).lower() == lowered:
                return str(header.get("value", "")).strip()
        return ""

    def list_messages(self, query: str, max_results: int) -> list[dict[str, Any]]:
        service = self._service()
        listing = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        out: list[dict[str, Any]] = []
        for item in listing.get("messages", []) or []:
            msg_id = item.get("id")
            if not msg_id:
                continue
            full = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                )
                .execute()
            )
            headers = full.get("payload", {}).get("headers", []) or []
            out.append(
                {
                    "id": str(full.get("id", "")),
                    "thread_id": str(full.get("threadId", "")),
                    "from": self._header_value(headers, "From"),
                    "to": self._header_value(headers, "To"),
                    "subject": self._header_value(headers, "Subject"),
                    "date": self._header_value(headers, "Date"),
                    "snippet": str(full.get("snippet", "")),
                }
            )
        return out

    def send_message(
        self,
        *,
        to: list[str],
        cc: list[str] | None,
        bcc: list[str] | None,
        subject: str,
        body_text: str | None,
        body_html: str | None,
    ) -> dict[str, Any]:
        service = self._service()

        message = EmailMessage()
        message["To"] = ", ".join([addr for addr in to if addr])
        if cc:
            message["Cc"] = ", ".join([addr for addr in cc if addr])
        if bcc:
            message["Bcc"] = ", ".join([addr for addr in bcc if addr])
        message["Subject"] = subject

        plain = (body_text or "").strip()
        html = (body_html or "").strip()
        if plain:
            message.set_content(plain)
        elif html:
            message.set_content("This message contains HTML content.")
        else:
            message.set_content("")
        if html:
            message.add_alternative(html, subtype="html")

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        return {
            "id": str(sent.get("id", "")),
            "thread_id": str(sent.get("threadId", "")),
            "label_ids": sent.get("labelIds", []),
        }
