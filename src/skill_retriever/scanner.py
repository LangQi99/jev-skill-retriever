"""Scan Hermes skill directories and expose compact skill metadata."""

from __future__ import annotations

from pathlib import Path

import yaml


def _frontmatter(content: str) -> dict:
    if not content.startswith("---\n"):
        return {}
    marker = content.find("\n---", 4)
    if marker == -1:
        return {}
    parsed = yaml.safe_load(content[4:marker]) or {}
    return parsed if isinstance(parsed, dict) else {}


def scan_hermes_skills(skills_dir: Path | str) -> list[dict]:
    """Recursively scan ``SKILL.md`` files, deduplicating by skill name."""
    root = Path(skills_dir)
    if not root.is_dir():
        return []

    by_name: dict[str, dict] = {}
    for skill_file in sorted(root.rglob("SKILL.md")):
        try:
            content = skill_file.read_text(encoding="utf-8")
            metadata = _frontmatter(content)
        except (OSError, UnicodeError, yaml.YAMLError):
            continue

        name = str(metadata.get("name") or skill_file.parent.name)
        hermes = metadata.get("metadata", {}).get("hermes", {})
        if not isinstance(hermes, dict):
            hermes = {}
        relative = skill_file.relative_to(root)
        category = str(
            hermes.get("category")
            or (relative.parts[0] if len(relative.parts) > 2 else "uncategorized")
        )
        by_name.setdefault(
            name,
            {
                "id": skill_file.parent.name,
                "name": name,
                "description": str(metadata.get("description") or ""),
                "category": category,
                "tags": hermes.get("tags", []),
                "path": str(skill_file.resolve()),
            },
        )

    return sorted(by_name.values(), key=lambda skill: skill["name"].lower())


def scan_skill_content(name: str, skills_dir: Path | str) -> str | None:
    """Return the complete ``SKILL.md`` for an exact skill name or directory."""
    root = Path(skills_dir)
    if not root.is_dir():
        return None
    for skill_file in sorted(root.rglob("SKILL.md")):
        try:
            content = skill_file.read_text(encoding="utf-8")
            metadata = _frontmatter(content)
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if str(metadata.get("name") or skill_file.parent.name) == name:
            return content
    return None
