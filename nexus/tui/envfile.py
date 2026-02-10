from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_ENV_LINE_RE = re.compile(r"^(\s*)(export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


@dataclass(slots=True)
class _EnvRow:
    raw: str
    key: str | None = None
    value: str | None = None
    quote: str = ""
    leading: str = ""
    has_export: bool = False


def _decode_value(value: str) -> tuple[str, str]:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        quote = stripped[0]
        inner = stripped[1:-1]
        if quote == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner, quote
    return stripped, ""


def _encode_value(value: str, quote_hint: str = "") -> str:
    quote = quote_hint if quote_hint in {"'", '"'} else ""
    if not quote:
        if not value:
            return ""
        needs_quotes = any(ch.isspace() for ch in value) or any(ch in value for ch in {'"', "'", "#"})
        quote = '"' if needs_quotes else ""
    if quote == "'":
        escaped_single = value.replace("'", "\\'")
        return f"'{escaped_single}'"
    if quote == '"':
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


class EnvFile:
    def __init__(self, path: Path, rows: list[_EnvRow]) -> None:
        self.path = path
        self.rows = rows
        self._values: dict[str, str] = {row.key: row.value or "" for row in rows if row.key}

    @classmethod
    def load(cls, path: Path) -> EnvFile:
        rows: list[_EnvRow] = []
        if not path.exists():
            return cls(path, rows)
        for raw in path.read_text(encoding="utf-8").splitlines():
            match = _ENV_LINE_RE.match(raw)
            if not match:
                rows.append(_EnvRow(raw=raw))
                continue
            leading, export_prefix, key, value_raw = match.groups()
            value, quote = _decode_value(value_raw)
            rows.append(
                _EnvRow(
                    raw=raw,
                    key=key,
                    value=value,
                    quote=quote,
                    leading=leading,
                    has_export=bool(export_prefix),
                )
            )
        return cls(path, rows)

    def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)

    def masked(self, key: str, reveal: int = 4) -> str:
        value = self._values.get(key, "")
        if not value:
            return ""
        if reveal <= 0:
            return "*" * len(value)
        if len(value) <= reveal:
            return "*" * len(value)
        return f"{'*' * (len(value) - reveal)}{value[-reveal:]}"

    def upsert(self, key: str, value: str) -> None:
        if key in self._values:
            self._values[key] = value
            return
        self.rows.append(_EnvRow(raw="", key=key, value=value))
        self._values[key] = value

    def as_dict(self) -> dict[str, str]:
        return dict(self._values)

    def render(self) -> str:
        lines: list[str] = []
        seen: set[str] = set()
        for row in self.rows:
            if not row.key:
                lines.append(row.raw)
                continue
            value = self._values.get(row.key, row.value or "")
            encoded = _encode_value(value, quote_hint=row.quote)
            export_prefix = "export " if row.has_export else ""
            lines.append(f"{row.leading}{export_prefix}{row.key}={encoded}")
            seen.add(row.key)
        for key, value in self._values.items():
            if key in seen:
                continue
            lines.append(f"{key}={_encode_value(value)}")
        body = "\n".join(lines)
        return f"{body}\n" if body else ""

    def write(self, path: Path | None = None) -> None:
        target = path or self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.render(), encoding="utf-8")
