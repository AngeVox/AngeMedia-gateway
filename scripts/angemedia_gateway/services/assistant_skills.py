"""Read-only bundled assistant skill definitions.

Skills are markdown files with small frontmatter. The loader never imports or
executes code from skill files; it only returns whitelisted metadata and text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import config as C

SKILLS_ROOT = C.PROJECT_ROOT / "docs" / "assistant" / "skills"
SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
MAX_SKILL_BYTES = 64 * 1024


@dataclass(frozen=True)
class AssistantSkill:
    id: str
    title: str
    media_type: str
    allowed_tools: tuple[str, ...]
    body: str

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "media_type": self.media_type,
            "allowed_tools": list(self.allowed_tools),
        }


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text.strip()
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text.strip()
    frontmatter = text[4:end].strip()
    body = text[end + 4 :].strip()
    data: dict[str, Any] = {}
    current_key = ""
    for raw_line in frontmatter.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, []).append(line[4:].strip().strip('"'))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        if not value:
            data[current_key] = []
        elif value.startswith("[") and value.endswith("]"):
            items = [item.strip().strip('"') for item in value[1:-1].split(",") if item.strip()]
            data[current_key] = items
        else:
            data[current_key] = value.strip('"')
    return data, body


def _skill_path(skill_id: str) -> Path:
    if not SKILL_ID_RE.fullmatch(skill_id):
        raise ValueError("invalid assistant skill id")
    path = (SKILLS_ROOT / skill_id / "SKILL.md").resolve()
    root = SKILLS_ROOT.resolve()
    if root not in path.parents:
        raise ValueError("invalid assistant skill path")
    return path


def load_assistant_skill(skill_id: str) -> AssistantSkill:
    path = _skill_path(skill_id)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(skill_id)
    if path.stat().st_size > MAX_SKILL_BYTES:
        raise ValueError("assistant skill too large")
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)
    return AssistantSkill(
        id=str(meta.get("id") or skill_id),
        title=str(meta.get("title") or skill_id.replace("_", " ").title()),
        media_type=str(meta.get("media_type") or "general"),
        allowed_tools=tuple(str(item) for item in meta.get("allowed_tools") or []),
        body=body,
    )


def select_prompt_skill(media_type: str) -> AssistantSkill:
    return load_assistant_skill("video_prompt_planner" if media_type == "video" else "image_prompt_planner")


def safe_tool_event(tool: str, summary: str, *, status: str = "done") -> dict[str, str]:
    return {
        "type": "tool",
        "tool": tool,
        "status": status,
        "summary": " ".join(str(summary or "").split())[:240],
    }


def skill_event(skill: AssistantSkill) -> dict[str, str]:
    return {
        "type": "skill",
        "skill": skill.id,
        "status": "selected",
        "summary": skill.title,
    }
