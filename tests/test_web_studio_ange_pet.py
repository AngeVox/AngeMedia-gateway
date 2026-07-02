"""Ange pet widget source contracts."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "app" / "www" / "assets" / "studio"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


class WebStudioAngePetContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.layout = read(STUDIO / "layout.js")
        cls.pet = read(STUDIO / "components" / "ange-pet.js")
        cls.chat = read(STUDIO / "components" / "assistant-chat.js")
        cls.i18n = read(STUDIO / "i18n.js")
        cls.css = read(STUDIO / "styles" / "shell.css")

    def test_shell_mounts_pet_once(self) -> None:
        self.assertIn("mountAngePet", self.layout)
        self.assertIn("let mounted = false", self.pet)
        self.assertIn("document.querySelector('[data-ange-pet=\"true\"]')", self.pet)

    def test_pet_opens_same_scoped_assistant_chat(self) -> None:
        self.assertIn("openAssistantChat", self.pet)
        self.assertIn("ange-pet-bot-head", self.pet)
        self.assertIn("ange-pet-bot-play", self.pet)
        self.assertIn("api.post('/assistant/chat'", self.chat)
        self.assertNotIn("/assistant/generate", self.pet)
        self.assertNotIn("fetch(", self.pet)

    def test_pet_is_draggable_but_mobile_degrades_to_fixed_entry(self) -> None:
        for token in ("pointerdown", "pointermove", "pointerup", "localStorage.setItem", "max-width: 640px"):
            self.assertIn(token, self.pet + self.css)
        self.assertIn("dockToEdge", self.pet)
        self.assertIn("undock", self.pet)
        self.assertIn("IDLE_DOCK_MS", self.pet)
        self.assertIn("ange-pet-peek", self.pet + self.css)
        self.assertIn("ange-pet-stuck-left", self.pet + self.css)
        self.assertIn("left: auto !important", self.css)
        self.assertIn("bottom: 12px !important", self.css)

    def test_pet_i18n_exists(self) -> None:
        self.assertIn("angePet.title", self.i18n)
        self.assertIn("angePet.label", self.i18n)


if __name__ == "__main__":
    unittest.main()
