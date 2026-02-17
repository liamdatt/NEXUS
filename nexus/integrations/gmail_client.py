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

    @classmethod
    def _message_metadata(cls, full: dict[str, Any]) -> dict[str, Any]:
        headers = full.get("payload", {}).get("headers", []) or []
        return {
            "id": str(full.get("id", "")),
            "thread_id": str(full.get("threadId", "")),
            "from": cls._header_value(headers, "From"),
            "to": cls._header_value(headers, "To"),
            "subject": cls._header_value(headers, "Subject"),
            "date": cls._header_value(headers, "Date"),
            "snippet": str(full.get("snippet", "")),
        }

    def search_threads(self, query: str, max_results: int) -> list[dict[str, Any]]:
        service = self._service()
        listing = (
            service.users()
            .threads()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        out: list[dict[str, Any]] = []
        for item in listing.get("threads", []) or []:
            thread_id = item.get("id")
            if not thread_id:
                continue
            full = (
                service.users()
                .threads()
                .get(
                    userId="me",
                    id=thread_id,
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                )
                .execute()
            )
            messages = full.get("messages", []) or []
            latest = messages[-1] if messages else {}
            meta = (
                self._message_metadata(latest)
                if isinstance(latest, dict) and latest
                else {
                    "id": "",
                    "thread_id": str(thread_id),
                    "from": "",
                    "to": "",
                    "subject": "",
                    "date": "",
                    "snippet": str(full.get("snippet", "")),
                }
            )
            meta["thread_id"] = str(thread_id)
            meta["message_count"] = len(messages)
            out.append(meta)
        return out

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
            out.append(self._message_metadata(full))
        return out

    @staticmethod
    def _build_message(
        *,
        to: list[str],
        cc: list[str] | None,
        bcc: list[str] | None,
        subject: str,
        body_text: str | None,
        body_html: str | None,
        reply_to_message_id: str | None,
    ) -> EmailMessage:
        message = EmailMessage()
        message["To"] = ", ".join([addr for addr in to if addr])
        if cc:
            message["Cc"] = ", ".join([addr for addr in cc if addr])
        if bcc:
            message["Bcc"] = ", ".join([addr for addr in bcc if addr])
        message["Subject"] = subject
        if reply_to_message_id:
            message["In-Reply-To"] = reply_to_message_id
            message["References"] = reply_to_message_id

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
        return message

    def send_message(
        self,
        *,
        to: list[str],
        cc: list[str] | None,
        bcc: list[str] | None,
        subject: str,
        body_text: str | None,
        body_html: str | None,
        reply_to_message_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        service = self._service()
        message = self._build_message(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            reply_to_message_id=reply_to_message_id,
        )

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        payload: dict[str, Any] = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        sent = (
            service.users()
            .messages()
            .send(userId="me", body=payload)
            .execute()
        )
        return {
            "id": str(sent.get("id", "")),
            "thread_id": str(sent.get("threadId", "")),
            "label_ids": sent.get("labelIds", []),
        }

    def create_draft(
        self,
        *,
        to: list[str],
        cc: list[str] | None,
        bcc: list[str] | None,
        subject: str,
        body_text: str | None,
        body_html: str | None,
        reply_to_message_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        service = self._service()
        message = self._build_message(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            reply_to_message_id=reply_to_message_id,
        )
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        payload: dict[str, Any] = {"message": {"raw": raw}}
        if thread_id:
            payload["message"]["threadId"] = thread_id
        draft = service.users().drafts().create(userId="me", body=payload).execute()
        message_obj = draft.get("message", {}) if isinstance(draft, dict) else {}
        return {
            "id": str(draft.get("id", "")),
            "message_id": str(message_obj.get("id", "")),
            "thread_id": str(message_obj.get("threadId", "")),
        }

    def send_draft(self, draft_id: str) -> dict[str, Any]:
        service = self._service()
        sent = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
        message_obj = sent.get("message", {}) if isinstance(sent, dict) else {}
        return {
            "id": str(message_obj.get("id", "")),
            "thread_id": str(message_obj.get("threadId", "")),
            "draft_id": str(sent.get("id", "")),
        }
