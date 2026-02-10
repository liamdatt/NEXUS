from __future__ import annotations

import re
from pathlib import Path


_DAILY_NOTE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def split_sections(memory_text: str) -> list[str]:
    sections = []
    current = []
    for line in memory_text.splitlines():
        if line.startswith("#") and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return [section for section in sections if section]


def score_section(section: str, query: str) -> int:
    tokens = [t for t in re.findall(r"[A-Za-z0-9_]+", query.lower()) if len(t) > 2]
    if not tokens:
        return 0
    lower = section.lower()
    return sum(lower.count(token) for token in tokens)


def select_relevant_sections(memory_text: str, query: str, limit: int = 3) -> list[str]:
    sections = split_sections(memory_text)
    ranked = sorted(((score_section(section, query), section) for section in sections), reverse=True)
    selected = [section for score, section in ranked if score > 0]
    if selected:
        return selected[:limit]
    return sections[:limit]


def list_recent_daily_note_paths(memories_dir: Path, days: int = 5) -> list[Path]:
    if days <= 0 or not memories_dir.exists():
        return []
    candidates = [
        path
        for path in memories_dir.iterdir()
        if path.is_file() and _DAILY_NOTE_RE.match(path.name)
    ]
    # File names are YYYY-MM-DD.md, so lexical sort == date sort.
    candidates.sort(reverse=True)
    return candidates[:days]
