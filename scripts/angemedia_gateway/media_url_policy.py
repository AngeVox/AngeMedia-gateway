"""Media URL and generated-file path helpers."""
from __future__ import annotations

import urllib.parse

from . import config as C


def is_http_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def is_trusted_remote_media_url(url: str, trusted_hosts: list[str] | tuple[str, ...] | set[str] | None = None) -> bool:
    hosts = {str(host).strip().lower() for host in (trusted_hosts or []) if str(host).strip()}
    if not hosts:
        return False
    value = str(url or "").strip()
    parsed = urllib.parse.urlparse(value)
    host = (parsed.hostname or "").strip().lower()
    return (
        parsed.scheme == "https"
        and host in hosts
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
        and bool(parsed.path)
    )


def is_generated_local_url(url: str) -> bool:
    if not url:
        return False
    if url.startswith(f"{C.PUBLIC_BASE_URL}/generated/"):
        return True
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme or parsed.netloc:
        public = urllib.parse.urlparse(C.PUBLIC_BASE_URL)
        return (
            parsed.scheme == public.scheme
            and parsed.netloc == public.netloc
            and parsed.path.startswith("/generated/")
        )
    return parsed.path.startswith("/generated/")


def generated_url_local_path(url: str) -> str:
    if not is_generated_local_url(url):
        return ""
    parsed = urllib.parse.urlparse(url)
    relative = parsed.path[len("/generated/"):] if parsed.path.startswith("/generated/") else ""
    if not relative or any(part in {"", ".", ".."} for part in relative.split("/")):
        return ""
    try:
        resolved = (C.OUTPUT_DIR / relative).resolve()
        resolved.relative_to(C.OUTPUT_DIR.resolve())
    except (OSError, ValueError):
        return ""
    return str(resolved) if resolved.is_file() else ""
