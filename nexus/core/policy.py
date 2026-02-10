from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from nexus.core.protocol import PendingAction
from nexus.db.models import Database


YES = {"y", "yes", "approve", "confirm", "proceed"}
NO = {"n", "no", "deny", "cancel", "stop"}


class PolicyEngine:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create_pending_action(
        self,
        chat_id: str,
        tool_name: str,
        risk_level: str,
        proposed_args: dict,
        ttl_minutes: int = 10,
    ) -> PendingAction:
        now = datetime.now(timezone.utc)
        action = PendingAction(
            action_id=str(uuid4()),
            tool_name=tool_name,
            risk_level=risk_level if risk_level in {"low", "medium", "high"} else "medium",
            expires_at=now + timedelta(minutes=ttl_minutes),
            proposed_args=proposed_args,
            chat_id=chat_id,
        )
        self.db.insert_pending_action(action)
        return action

    def parse_confirmation(self, text: str) -> str | None:
        lowered = text.strip().lower()
        if lowered in YES:
            return "approved"
        if lowered in NO:
            return "denied"
        return None

    def resolve_pending_action_from_text(self, chat_id: str, text: str) -> PendingAction | None:
        decision = self.parse_confirmation(text)
        if not decision:
            return None
        pending = self.db.get_latest_pending_action(chat_id)
        if not pending:
            return None

        now = datetime.now(timezone.utc)
        expires_at = datetime.fromisoformat(pending["expires_at"])
        if expires_at < now:
            self.db.update_pending_status(pending["action_id"], "expired")
            return None

        self.db.update_pending_status(pending["action_id"], decision)
        pending["status"] = decision
        pending["proposed_args"] = json.loads(pending["proposed_args"])
        return PendingAction(**pending)
