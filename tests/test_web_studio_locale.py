"""Web Studio locale selection runtime contracts."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
I18N_JS = ROOT / "app" / "www" / "assets" / "studio" / "i18n.js"

RUNNER = r"""
import { pathToFileURL } from 'node:url';

const [modulePath, rawConfig] = process.argv.slice(2);
const config = JSON.parse(rawConfig);

if (config.navigator === null) {
  try { delete globalThis.navigator; } catch (_) {}
} else {
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: {
      language: config.navigator.language,
      languages: config.navigator.languages,
    },
  });
}

let stored = config.stored;
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: {
    getItem() {
      if (config.readError) throw new Error('storage read failed');
      return stored;
    },
    setItem(_key, value) {
      if (config.writeError) throw new Error('storage write failed');
      stored = String(value);
    },
  },
});

Object.defineProperty(globalThis, 'document', {
  configurable: true,
  value: { documentElement: { lang: 'initial' } },
});

const moduleUrl = `${pathToFileURL(modulePath).href}?case=${encodeURIComponent(config.caseId)}`;
const locale = await import(moduleUrl);
const result = {
  initialLanguage: locale.getLanguage(),
  initialDocumentLanguage: globalThis.document.documentElement.lang,
  defaultLanguage: locale.defaultLanguage,
  stored,
};

if (config.setTo !== null) {
  locale.setLanguage(config.setTo);
  result.afterSetLanguage = locale.getLanguage();
  result.afterSetDocumentLanguage = globalThis.document.documentElement.lang;
  result.afterSetStored = stored;
}

process.stdout.write(JSON.stringify(result));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the locale runtime contract")
class WebStudioLocaleRuntimeTest(unittest.TestCase):
    def run_case(
        self,
        *,
        navigator_language: str | None,
        navigator_languages: list[str] | None = None,
        stored: str | None = None,
        read_error: bool = False,
        write_error: bool = False,
        set_to: str | None = None,
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            module_path = temp / "i18n.mjs"
            runner_path = temp / "runner.mjs"
            module_path.write_text(I18N_JS.read_text(encoding="utf-8"), encoding="utf-8")
            runner_path.write_text(RUNNER, encoding="utf-8")
            navigator = None
            if navigator_language is not None:
                navigator = {
                    "language": navigator_language,
                    "languages": navigator_languages or [navigator_language],
                }
            config = {
                "caseId": self.id(),
                "navigator": navigator,
                "stored": stored,
                "readError": read_error,
                "writeError": write_error,
                "setTo": set_to,
            }
            completed = subprocess.run(
                ["node", str(runner_path), str(module_path), json.dumps(config)],
                check=True,
                capture_output=True,
                text=True,
            )
            return json.loads(completed.stdout)

    def test_saved_language_has_priority_over_browser_language(self) -> None:
        result = self.run_case(navigator_language="zh-CN", stored="en-US")
        self.assertEqual(result["initialLanguage"], "en-US")
        self.assertEqual(result["initialDocumentLanguage"], "en-US")

        result = self.run_case(navigator_language="en-US", stored="zh-CN")
        self.assertEqual(result["initialLanguage"], "zh-CN")
        self.assertEqual(result["initialDocumentLanguage"], "zh-CN")

    def test_chinese_browser_variants_default_to_simplified_chinese(self) -> None:
        for language in ("zh-CN", "zh-HK", "zh-TW", "ZH-cn"):
            with self.subTest(language=language):
                result = self.run_case(navigator_language=language)
                self.assertEqual(result["initialLanguage"], "zh-CN")
                self.assertEqual(result["initialDocumentLanguage"], "zh-CN")

    def test_non_chinese_browsers_default_to_english(self) -> None:
        for language in ("en-US", "ko-KR", "ja-JP", "fr-FR"):
            with self.subTest(language=language):
                result = self.run_case(navigator_language=language)
                self.assertEqual(result["initialLanguage"], "en-US")
                self.assertEqual(result["initialDocumentLanguage"], "en-US")

    def test_first_browser_preference_is_used(self) -> None:
        result = self.run_case(
            navigator_language="en-US",
            navigator_languages=["zh-HK", "en-US"],
        )
        self.assertEqual(result["initialLanguage"], "zh-CN")

    def test_storage_read_failure_still_uses_browser_language(self) -> None:
        result = self.run_case(navigator_language="en-GB", read_error=True)
        self.assertEqual(result["initialLanguage"], "en-US")
        self.assertEqual(result["initialDocumentLanguage"], "en-US")

    def test_missing_browser_context_preserves_chinese_fallback(self) -> None:
        result = self.run_case(navigator_language=None)
        self.assertEqual(result["defaultLanguage"], "zh-CN")
        self.assertEqual(result["initialLanguage"], "zh-CN")
        self.assertEqual(result["initialDocumentLanguage"], "zh-CN")

    def test_manual_language_change_updates_storage_and_document_metadata(self) -> None:
        result = self.run_case(navigator_language="zh-CN", set_to="en-US")
        self.assertEqual(result["afterSetLanguage"], "en-US")
        self.assertEqual(result["afterSetDocumentLanguage"], "en-US")
        self.assertEqual(result["afterSetStored"], "en-US")


if __name__ == "__main__":
    unittest.main()
