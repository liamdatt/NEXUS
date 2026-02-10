import pytest

from nexus.core.decision import DecisionParseError, parse_agent_decision


def test_decision_schema_valid_call():
    decision = parse_agent_decision(
        {
            "thought": "Need tool output first.",
            "call": {"name": "web", "arguments": {"action": "search_web", "query": "jamaica"}},
        }
    )
    assert decision.call is not None
    assert decision.call.name == "web"
    assert decision.response is None


def test_decision_schema_valid_response():
    decision = parse_agent_decision({"thought": "Done.", "response": "Here is the answer."})
    assert decision.call is None
    assert decision.response == "Here is the answer."


def test_decision_schema_rejects_both_call_and_response():
    with pytest.raises(DecisionParseError, match="exactly one of call or response is required"):
        parse_agent_decision(
            {
                "thought": "Ambiguous.",
                "call": {"name": "web", "arguments": {}},
                "response": "done",
            }
        )


def test_decision_schema_rejects_neither_call_nor_response():
    with pytest.raises(DecisionParseError, match="exactly one of call or response is required"):
        parse_agent_decision({"thought": "No action"})


def test_decision_schema_rejects_invalid_call_arguments_type():
    with pytest.raises(DecisionParseError, match="call.arguments"):
        parse_agent_decision(
            {
                "thought": "bad args",
                "call": {"name": "web", "arguments": ["not", "an", "object"]},
            }
        )
