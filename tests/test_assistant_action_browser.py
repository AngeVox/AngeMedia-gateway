"""浏览器状态机合同纳入常规pytest，验证确认去重、响应丢失和账户切换。"""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), '需要Node执行前端状态机')
class AssistantActionsBrowserTest(unittest.TestCase):
    def test_action_controller_runtime_contract(self):
        result = subprocess.run([shutil.which('node'), '--test', 'tests/assistant_actions_controller.mjs',
            'tests/assistant_human_presentation.mjs', 'tests/assistant_chat_browser_state.mjs'],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__': unittest.main()
