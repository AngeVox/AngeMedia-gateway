"""Web Studio assistant chat source contracts."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "app" / "www" / "assets" / "studio"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


class WebStudioAssistantChatContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.layout = read(STUDIO / "layout.js")
        cls.chat = read(STUDIO / "components" / "assistant-chat.js")
        cls.i18n = read(STUDIO / "i18n.js")
        cls.css = read(STUDIO / "styles" / "assistant.css")
        cls.index = read(ROOT / "app" / "www" / "index.html")

    def test_topbar_assistant_opens_scoped_chat(self) -> None:
        self.assertIn("openAssistantChat", self.layout)
        self.assertNotIn("openAssistantPlanner({ currentPage: 'topbar'", self.layout)
        self.assertNotIn("showWipFeature", self.layout)

    def test_chat_calls_formal_assistant_chat_api(self) -> None:
        self.assertIn("api.post('/assistant/chat'", self.chat)
        self.assertIn("session_id: sessionId", self.chat)
        self.assertIn("result.timeline", self.chat)
        self.assertIn("openAssistantSettings", self.chat)
        self.assertIn("assistantChat.settings", self.chat)
        self.assertNotIn("/assistant/generate", self.chat)
        self.assertNotIn("fetch(", self.chat)
        self.assertNotIn("setInterval", self.chat)

    def test_chat_enter_sends_and_shift_enter_keeps_newline(self) -> None:
        self.assertIn("event.key === 'Enter' && !event.shiftKey", self.chat)
        self.assertNotIn("event.ctrlKey || event.metaKey", self.chat)

    def test_chat_i18n_and_scope_copy_exist(self) -> None:
        for key in (
            "assistantChat.title",
            "assistantChat.copy",
            "assistantChat.scope",
            "assistantChat.refused",
            "assistantChat.send",
            "assistantChat.settings",
            "assistantChat.timeline.llm_chat",
            "assistantChat.timeline.local_kb_search",
        ):
            self.assertIn(key, self.i18n)

    def test_no_raw_sensitive_fields_rendered(self) -> None:
        for token in ("request_hash", "input_json", "output_json", "provider_raw_body", "Authorization"):
            self.assertNotIn(token, self.chat)

    def test_390px_layout_contract_for_chat(self) -> None:
        self.assertIn("styles/assistant.css", self.index)
        self.assertIn(".assistant-chat-modal", self.css)
        self.assertIn(".assistant-chat-messages", self.css)
        mobile = re.search(r"@media \(max-width: 400px\)\s*\{(?P<body>.*?)\n\}", self.css, re.S)
        self.assertIsNotNone(mobile)
        body = mobile.group("body")
        self.assertIn(".assistant-chat-modal", body)
        self.assertIn(".assistant-chat-actions", body)


if __name__ == "__main__":
    unittest.main()
