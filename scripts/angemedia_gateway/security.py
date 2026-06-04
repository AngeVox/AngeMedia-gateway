"""安全工具：密码哈希、SSRF 防护、令牌生成与脱敏。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import socket
import urllib.parse

TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

BLOCKED_SSRF_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{12,})"),
    re.compile(r"(am-[A-Za-z0-9_\-]{12,})"),
    re.compile(r"(av-[A-Za-z0-9_\-]{12,})"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{12,}", re.I),
]


def generate_gateway_key() -> str:
    return "am-" + secrets.token_hex(16)


def generate_session_token() -> str:
    return "ams-" + secrets.token_hex(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str, iterations: int = 240000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.b64decode(salt_text.encode("ascii"))
        expected = base64.b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def validate_task_id(task_id: str) -> str:
    if not TASK_ID_RE.fullmatch(task_id):
        raise ValueError("task_id 只允许字母、数字、下划线和连字符，长度 1-128")
    return task_id


def validate_public_http_url(url: str) -> str:
    """校验 URL 不指向本机、内网、链路本地或保留地址，并保留原始 path/query。

    用于远端媒体下载时不能像 base_url 那样简单 rstrip("/"), 否则可能破坏带签名
    的对象存储 URL。
    """
    value = str(url or "").strip()
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL 只允许 http 或 https")
    if not parsed.hostname:
        raise ValueError("URL 缺少 hostname")
    if parsed.port is not None and not (1 <= parsed.port <= 65535):
        raise ValueError("URL 端口必须在 1-65535 范围内")

    host = parsed.hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("拒绝访问 localhost 地址")

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"URL 解析失败：{host}") from exc

    checked: set[str] = set()
    for info in infos:
        ip_text = info[4][0]
        if ip_text in checked:
            continue
        checked.add(ip_text)
        ip = ipaddress.ip_address(ip_text)
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or any(ip in network for network in BLOCKED_SSRF_NETWORKS)
        ):
            raise ValueError(f"拒绝访问内网或保留地址：{ip}")
    return value


def ensure_public_http_url(url: str) -> str:
    """校验公开 HTTP(S) base_url，并去掉末尾斜杠以便拼接 API 路径。"""
    return validate_public_http_url(url).rstrip("/")


def redact_secret_text(text: str) -> str:
    value = str(text)
    for pattern in SECRET_PATTERNS:
        if pattern.pattern.startswith("(Bearer"):
            value = pattern.sub(r"\1***REDACTED***", value)
        else:
            value = pattern.sub("***REDACTED***", value)
    return value
