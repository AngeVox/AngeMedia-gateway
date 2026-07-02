"""assistant.py call_llm_for_plan error message safety tests.

Verify that exception messages never contain raw upstream body, secrets,
or LLM content — even when the upstream response includes them.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRET_MARKER = "sk-LEAKED-SECRET-MUST-NOT-APPEAR-1234567890abcdef"
HTML_BODY = "<html><body>Internal Server Error</body></html>"
TOKEN_BODY = '{"error": "Authorization: Bearer sk-1234567890abcdef failed"}'

_CONFIG_OVERRIDES: dict[str, str] = {
    "ANGE_ASSISTANT_ENABLED": "true",
    "ANGE_LLM_API_KEY": "sk-test-assistant-key-12345",
    "ANGE_LLM_BASE_URL": "https://llm.example.com/v1",
    "ANGE_LLM_MODEL": "test-model",
    "ANGE_LLM_TIMEOUT": "60",
    "ANGE_LLM_TEMPERATURE": "0.35",
}


def _fake_get_config(key: str, default: str = "") -> str:
    if key in _CONFIG_OVERRIDES:
        return _CONFIG_OVERRIDES[key]
    return os.getenv(key, default)


def _make_response(status_code: int = 200, text: str = "", json_data: Any = None) -> MagicMock:
    """Build a mock httpx.Response with controlled content."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("No JSON")
    return resp


def _mock_httpx(response: MagicMock) -> tuple[Any, MagicMock]:
    """Patch the shared outbound AsyncClient.

    Returns (patcher, mock_client) — caller must assert mock_client.post
    was awaited to guard against early-return false greens.
    """
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return patch("angemedia_gateway.outbound_http.httpx.AsyncClient", return_value=mock_client), mock_client


class _AssistantTestBase(unittest.TestCase):
    """Base that enables the assistant with a mocked get_config and httpx."""

    def setUp(self) -> None:
        self._patches = [
            patch("angemedia_gateway.assistant.get_config", side_effect=_fake_get_config),
            patch.dict(os.environ, {"ANGE_ASSISTANT_ENABLED": "true"}, clear=False),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()


class AssistantHttpErrorNoRawBodyTest(_AssistantTestBase):
    """HTTP 4xx/5xx 上游响应 body 中的 secret 不得出现在异常 message 中。"""

    def _run(self, status_code: int, body: str, expect_status: bool = True) -> None:
        from angemedia_gateway.assistant import call_llm_for_plan
        from angemedia_gateway.schemas import AssistantRequest
        from angemedia_gateway.providers.errors import BackendUnavailable

        resp = _make_response(status_code=status_code, text=body)
        httpx_patcher, mock_client = _mock_httpx(resp)
        with httpx_patcher:
            req = AssistantRequest(prompt="test", media_type="image")
            with self.assertRaises(BackendUnavailable) as ctx:
                asyncio.run(call_llm_for_plan(req))
            mock_client.post.assert_awaited_once()
            msg = str(ctx.exception)
            self.assertNotIn(SECRET_MARKER, msg, f"secret leaked into exception: {msg}")
            self.assertNotIn("<html>", msg, f"HTML body leaked into exception: {msg}")
            self.assertNotIn("Bearer", msg, f"Bearer token leaked into exception: {msg}")
            self.assertNotIn("sk-", msg, f"API key prefix leaked into exception: {msg}")
            if expect_status:
                self.assertIn(str(status_code), msg, "status_code should be in message")

    def test_http_500_with_secret_in_body(self) -> None:
        self._run(500, f"Error: {SECRET_MARKER}")

    def test_http_500_with_html_body(self) -> None:
        self._run(500, HTML_BODY)

    def test_http_502_with_token_in_body(self) -> None:
        self._run(502, TOKEN_BODY)


class AssistantNonJsonNoRawBodyTest(_AssistantTestBase):
    """上游返回非 JSON body 中的 secret 不得出现在异常 message 中。"""

    def _run(self, body: str) -> None:
        from angemedia_gateway.assistant import call_llm_for_plan
        from angemedia_gateway.schemas import AssistantRequest
        from angemedia_gateway.providers.errors import BackendUnavailable

        resp = _make_response(status_code=200, text=body)
        httpx_patcher, mock_client = _mock_httpx(resp)
        with httpx_patcher:
            req = AssistantRequest(prompt="test", media_type="image")
            with self.assertRaises(BackendUnavailable) as ctx:
                asyncio.run(call_llm_for_plan(req))
            mock_client.post.assert_awaited_once()
            msg = str(ctx.exception)
            self.assertNotIn(SECRET_MARKER, msg, f"secret leaked into exception: {msg}")
            self.assertNotIn("<html>", msg, f"HTML body leaked into exception: {msg}")
            self.assertNotIn("Bearer", msg, f"Bearer token leaked into exception: {msg}")

    def test_non_json_with_secret(self) -> None:
        self._run(f"not json at all: {SECRET_MARKER}")

    def test_non_json_html(self) -> None:
        self._run(HTML_BODY)


class AssistantBadContentNoRawBodyTest(_AssistantTestBase):
    """LLM 返回 JSON 但 content 不可解析时，content 中的 secret 不得出现在异常 message 中。"""

    def test_secret_in_content_not_leaked(self) -> None:
        from angemedia_gateway.assistant import call_llm_for_plan
        from angemedia_gateway.schemas import AssistantRequest
        from angemedia_gateway.providers.errors import BackendUnavailable

        json_data = {
            "choices": [
                {"message": {"content": f"This is not JSON: {SECRET_MARKER}"}}
            ]
        }
        resp = _make_response(status_code=200, text="ok", json_data=json_data)
        httpx_patcher, mock_client = _mock_httpx(resp)
        with httpx_patcher:
            req = AssistantRequest(prompt="test", media_type="image")
            with self.assertRaises(BackendUnavailable) as ctx:
                asyncio.run(call_llm_for_plan(req))
            mock_client.post.assert_awaited_once()
            msg = str(ctx.exception)
            self.assertNotIn(SECRET_MARKER, msg, f"secret leaked into exception: {msg}")
            self.assertNotIn("Bearer", msg, f"Bearer token leaked into exception: {msg}")
            self.assertNotIn("sk-", msg, f"API key prefix leaked into exception: {msg}")

    def test_secret_in_json_non_dict_content_not_leaked(self) -> None:
        """content 是合法 JSON 字符串但不是 dict 时也不泄露。"""
        from angemedia_gateway.assistant import call_llm_for_plan
        from angemedia_gateway.schemas import AssistantRequest
        from angemedia_gateway.providers.errors import BackendUnavailable

        json_data = {
            "choices": [
                {"message": {"content": f'"{SECRET_MARKER}"'}}
            ]
        }
        resp = _make_response(status_code=200, text="ok", json_data=json_data)
        httpx_patcher, mock_client = _mock_httpx(resp)
        with httpx_patcher:
            req = AssistantRequest(prompt="test", media_type="image")
            with self.assertRaises(BackendUnavailable) as ctx:
                asyncio.run(call_llm_for_plan(req))
            mock_client.post.assert_awaited_once()
            msg = str(ctx.exception)
            self.assertNotIn(SECRET_MARKER, msg, f"secret leaked into exception: {msg}")


if __name__ == "__main__":
    unittest.main()
