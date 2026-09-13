"""Bundled read-only knowledge retrieval for the AngeMedia assistant."""
from __future__ import annotations

import re
from typing import Any

from .. import config as C
from ..security import redact_secret_text

KB_ROOT = C.PROJECT_ROOT / "docs" / "assistant" / "kb"
_MAX_DOC_BYTES = 128 * 1024


def _safe_text(value: Any, *, limit: int = 900) -> str:
    text = redact_secret_text(str(value or ""))
    text = re.sub(r"\bAuthorization\b(?:\s*:\s*Bearer\s+[^\s,;]+)?", "[redacted auth]", text, flags=re.I)
    text = re.sub(r"data:[A-Za-z0-9.+/-]+;base64,[A-Za-z0-9+/=_-]+", "[redacted data url]", text, flags=re.I)
    text = re.sub(r"\b[A-Za-z]:\\[^\s,;，。]+", "[redacted local path]", text)
    text = re.sub(r"(?<![A-Za-z0-9])/(?:root|home|tmp|var|mnt|vol\d*|app)(?:/[^\s,;，。)]*)?", "[redacted local path]", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def _documents() -> list[tuple[str, str]]:
    docs: list[tuple[str, str]] = []
    if not KB_ROOT.exists():
        return docs
    root = KB_ROOT.resolve()
    for path in sorted(KB_ROOT.glob("*.md")):
        try:
            resolved = path.resolve()
            if root not in resolved.parents or path.stat().st_size > _MAX_DOC_BYTES:
                continue
            docs.append((path.stem, path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return docs


def search_assistant_knowledge(message: str, *, limit: int = 4) -> list[dict[str, str]]:
    query = str(message or "").strip()
    bounded_limit = max(1, min(int(limit), 8))
    terms = {
        term.lower()
        for term in re.split(r"[\s,，。；;:/\\|()\[\]{}]+", query)
        if len(term.strip()) >= 2
    }
    hits: list[tuple[int, str, str]] = []
    for doc_id, body in _documents():
        for paragraph in re.split(r"\n\s*\n", body):
            clean = _safe_text(paragraph)
            if not clean or clean.startswith("# "):
                continue
            lowered = clean.lower()
            score = sum(1 for term in terms if term in lowered)
            if score:
                hits.append((score, doc_id, clean))
    hits.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [
        {"source": doc_id, "summary": summary}
        for _, doc_id, summary in hits[:bounded_limit]
    ]
