from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "app" / "www" / "assets" / "studio"


class WebStudioQueueBackendContractTest(unittest.TestCase):
    def test_local_queue_is_not_rendered_as_broker_failure(self) -> None:
        source = (STUDIO / "features" / "diagnostics" / "page.js").read_text(encoding="utf-8")
        i18n = (STUDIO / "i18n.js").read_text(encoding="utf-8")
        self.assertIn("queue.backend === 'local'", source)
        self.assertIn("diagnostics.localExecution", source)
        self.assertIn("本地执行 · 无需 Redis", i18n)
        self.assertIn("Local execution · Redis not required", i18n)


if __name__ == "__main__":
    unittest.main()
