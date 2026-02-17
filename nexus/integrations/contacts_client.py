from __future__ import annotations

from typing import Any

from nexus.config import Settings
from nexus.integrations.google_auth import load_google_credentials


class ContactsClient:
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
        return build("people", "v1", credentials=creds, cache_discovery=False)

    def list_contacts(self, max_results: int) -> list[dict[str, Any]]:
        service = self._service()
        response = (
            service.people()
            .connections()
            .list(
                resourceName="people/me",
                pageSize=max_results,
                personFields="names,emailAddresses,phoneNumbers,organizations",
                sortOrder="LAST_MODIFIED_DESCENDING",
            )
            .execute()
        )
        out: list[dict[str, Any]] = []
        for person in response.get("connections", []) or []:
            names = person.get("names", []) or []
            emails = person.get("emailAddresses", []) or []
            phones = person.get("phoneNumbers", []) or []
            organizations = person.get("organizations", []) or []
            out.append(
                {
                    "resource_name": str(person.get("resourceName", "")),
                    "display_name": str((names[0] or {}).get("displayName", "")) if names else "",
                    "emails": [str(item.get("value", "")) for item in emails if isinstance(item, dict)],
                    "phones": [str(item.get("value", "")) for item in phones if isinstance(item, dict)],
                    "organizations": [
                        str(item.get("name", ""))
                        for item in organizations
                        if isinstance(item, dict)
                    ],
                }
            )
        return out
