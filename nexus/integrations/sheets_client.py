from __future__ import annotations

from typing import Any

from nexus.config import Settings
from nexus.integrations.google_auth import load_google_credentials


class SheetsClient:
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
        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    def get_values(self, spreadsheet_id: str, range_a1: str) -> dict[str, Any]:
        service = self._service()
        return (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_a1)
            .execute()
        )

    def update_values(
        self,
        spreadsheet_id: str,
        range_a1: str,
        values: list[list[Any]],
        input_option: str,
    ) -> dict[str, Any]:
        service = self._service()
        return (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueInputOption=input_option,
                body={"values": values},
            )
            .execute()
        )

    def append_values(
        self,
        spreadsheet_id: str,
        range_a1: str,
        values: list[list[Any]],
        input_option: str,
        insert_option: str,
    ) -> dict[str, Any]:
        service = self._service()
        return (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueInputOption=input_option,
                insertDataOption=insert_option,
                body={"values": values},
            )
            .execute()
        )

    def clear_values(self, spreadsheet_id: str, range_a1: str) -> dict[str, Any]:
        service = self._service()
        return (
            service.spreadsheets()
            .values()
            .clear(spreadsheetId=spreadsheet_id, range=range_a1, body={})
            .execute()
        )

    def metadata(self, spreadsheet_id: str) -> dict[str, Any]:
        service = self._service()
        return (
            service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, includeGridData=False)
            .execute()
        )
