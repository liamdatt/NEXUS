from pathlib import Path

from nexus.core.policy import PolicyEngine
from nexus.db.models import Database


def test_policy_pending_action_confirmation(tmp_path: Path):
    db = Database(tmp_path / "nexus.db")
    policy = PolicyEngine(db)

    pending = policy.create_pending_action(
        chat_id="chat-1",
        tool_name="filesystem",
        risk_level="high",
        proposed_args={"tool": "filesystem", "args": {"action": "delete_file", "path": "a.txt"}},
    )

    resolved = policy.resolve_pending_action_from_text("chat-1", "YES")
    assert resolved is not None
    assert resolved.action_id == pending.action_id
    assert resolved.status == "approved"
