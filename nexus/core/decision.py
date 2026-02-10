from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class DecisionParseError(ValueError):
    pass


class DecisionCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("call.name must not be empty")
        return name


class AgentDecision(BaseModel):
    thought: str
    call: DecisionCall | None = None
    response: str | None = None

    @field_validator("thought")
    @classmethod
    def _validate_thought(cls, value: str) -> str:
        thought = value.strip()
        if not thought:
            raise ValueError("thought must not be empty")
        return thought

    @field_validator("response")
    @classmethod
    def _normalize_response(cls, value: str | None) -> str | None:
        if value is None:
            return None
        response = value.strip()
        if not response:
            raise ValueError("response must not be empty")
        return response

    @model_validator(mode="after")
    def _validate_exclusive_action(self) -> AgentDecision:
        has_call = self.call is not None
        has_response = self.response is not None
        if has_call == has_response:
            raise ValueError("exactly one of call or response is required")
        return self


def _extract_json_candidate(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped:
        return None

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    starts = [idx for idx in (stripped.find("{"), stripped.find("[")) if idx >= 0]
    for start in sorted(set(starts)):
        try:
            payload, _ = decoder.raw_decode(stripped[start:])
            return payload
        except json.JSONDecodeError:
            continue
    return None


def _coerce_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        parsed = _extract_json_candidate(payload)
        if parsed is None:
            raise DecisionParseError("decision must be valid JSON object")
        payload = parsed

    if isinstance(payload, list):
        if not payload:
            raise DecisionParseError("decision array is empty")
        payload = payload[0]

    if not isinstance(payload, dict):
        raise DecisionParseError("decision must be a JSON object")
    return payload


def _normalize_validation_error(exc: ValidationError) -> str:
    first = exc.errors()[0] if exc.errors() else {"msg": "invalid decision", "loc": ()}
    loc = ".".join(str(part) for part in first.get("loc", ()))
    msg = str(first.get("msg", "invalid decision"))
    if loc:
        return f"invalid decision at {loc}: {msg}"
    return f"invalid decision: {msg}"


def parse_agent_decision(payload: Any) -> AgentDecision:
    raw = _coerce_payload(payload)
    try:
        return AgentDecision.model_validate(raw)
    except ValidationError as exc:
        raise DecisionParseError(_normalize_validation_error(exc)) from exc
