from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus.config import Settings
from nexus.integrations.google_auth import load_google_credentials


class DriveClient:
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
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    def search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        service = self._service()
        request = (
            service.files()
            .list(
                q=query or None,
                pageSize=max_results,
                fields=(
                    "files(id,name,mimeType,modifiedTime,webViewLink,"
                    "owners(displayName,emailAddress))"
                ),
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
        )
        response = request.execute()
        out: list[dict[str, Any]] = []
        for item in response.get("files", []) or []:
            owners = item.get("owners", []) or []
            owner_names = [
                str(owner.get("displayName") or owner.get("emailAddress") or "").strip()
                for owner in owners
                if isinstance(owner, dict)
            ]
            out.append(
                {
                    "id": str(item.get("id", "")),
                    "name": str(item.get("name", "")),
                    "mime_type": str(item.get("mimeType", "")),
                    "modified_time": str(item.get("modifiedTime", "")),
                    "web_view_link": str(item.get("webViewLink", "")),
                    "owners": [owner for owner in owner_names if owner],
                }
            )
        return out

    def upload_file(
        self,
        file_path: str | Path,
        *,
        name: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        service = self._service()
        try:
            from googleapiclient.http import MediaFileUpload  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Google API media upload dependency missing. Reinstall project dependencies."
            ) from exc

        source = Path(file_path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"upload file not found: {source}")

        upload = MediaFileUpload(str(source), mimetype=mime_type or None, resumable=False)
        body: dict[str, Any] = {"name": name or source.name}
        created = (
            service.files()
            .create(
                body=body,
                media_body=upload,
                fields="id,name,mimeType,webViewLink,webContentLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        return {
            "id": str(created.get("id", "")),
            "name": str(created.get("name", "")),
            "mime_type": str(created.get("mimeType", "")),
            "web_view_link": str(created.get("webViewLink", "")),
            "web_content_link": str(created.get("webContentLink", "")),
        }
