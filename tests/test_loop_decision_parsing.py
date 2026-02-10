import pytest

from nexus.core.decision import DecisionParseError, parse_agent_decision


def test_parse_decision_accepts_call_shape():
    decision = parse_agent_decision(
        '{"thought":"Need to search first.","call":{"name":"web","arguments":{"action":"search_web","query":"jamaica"}}}'
    )
    assert decision.thought == "Need to search first."
    assert decision.call is not None
    assert decision.call.name == "web"
    assert decision.call.arguments["action"] == "search_web"
    assert decision.response is None


def test_parse_decision_accepts_response_shape():
    decision = parse_agent_decision('{"thought":"I have enough context.","response":"Jamaica is..."}')
    assert decision.thought == "I have enough context."
    assert decision.response == "Jamaica is..."
    assert decision.call is None


def test_parse_decision_rejects_plain_text():
    with pytest.raises(DecisionParseError, match="decision must be valid JSON object"):
        parse_agent_decision("Normal plain text response.")
