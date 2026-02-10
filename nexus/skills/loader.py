from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SkillDocument:
    name: str
    path: Path
    content: str


def load_skill_documents(skills_dir: Path) -> list[SkillDocument]:
    if not skills_dir.exists():
        return []
    skill_files = sorted(skills_dir.rglob("SKILL.md"))
    docs: list[SkillDocument] = []
    for path in skill_files:
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        name = path.parent.name
        docs.append(SkillDocument(name=name, path=path, content=content))
    return docs
