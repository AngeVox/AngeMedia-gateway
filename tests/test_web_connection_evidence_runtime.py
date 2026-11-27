"""执行真实前端投影函数，验证时效、时钟差与安全提示。"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "需要Node执行前端模块")
class WebConnectionEvidenceRuntimeTest(unittest.TestCase):
    def test_evidence_validity_and_server_clock_offset(self):
        script = r"""
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const {prepareConnectionEvidence: prepare,connectionEvidencePresentation: view} = await import(pathToFileURL(process.argv[1]).href);
const locale = await import(new URL('../../i18n.js',pathToFileURL(process.argv[1])));
locale.setLanguage('zh-CN');
const stamp = ms => new Date(ms).toISOString();
const observed = {status:'success',evidence_state:'current',server_time:stamp(1000000),expires_at:stamp(1001000)};
const prepared = prepare(observed,100);
assert.equal(prepared.deadline,1100);
assert.equal(view(prepared,1099).status,'success');
assert.equal(view(prepared,1100).status,'expired');
assert.equal(view(prepared,1100).tone,'warning');
assert.equal(view({...prepared,evidence_state:'configuration_changed'},101).status,'configuration_changed');
assert.equal(view({...prepared,status:'not_tested',evidence_state:'testing'},1100).status,'expired');
assert.equal(view({status:'success'},100).status,'not_tested');
assert.equal(view(prepare({status:'success',evidence_state:'current'},100),101).status,'not_tested');
assert.equal(view(prepare({...observed,status:'failed'},100),101).tone,'danger');
assert.match(view(prepared,101).message,/模型列表/);
assert.equal(view(undefined).status,'not_tested');
const modelScope = {...prepared, provider_id:'modelscope', model_list_validated:true,
  project_model_visibility:{listed:['Qwen/Qwen-Image-2512'],not_listed:['Tongyi-MAI/Z-Image']}};
const localized = view(modelScope,101).message;
assert.match(localized,/生成权限和结果尚未验证/);
assert.match(localized,/目录中列出的内置型号\nQwen\/Qwen-Image-2512/);
assert.match(localized,/本次目录未列出的内置型号\nTongyi-MAI\/Z-Image/);
assert.doesNotMatch(view(modelScope,1100).message,/Z-Image/);
assert.doesNotMatch(view({...modelScope,evidence_state:'configuration_changed'},101).message,/Z-Image/);
assert.doesNotMatch(view({...modelScope,model_list_validated:false},101).message,/Z-Image/);
const override = {provider_id:'modelscope',status:'unsupported',evidence_state:'current'};
assert.match(view(override,101).message,/魔搭官方地址/);
locale.setLanguage('en-US');
assert.match(view(prepared,101).message,/model list endpoint/);
assert.match(view(modelScope,101).message,/Generation access and output are unverified/);
assert.match(view(modelScope,101).message,/Built-in models absent from this list\nTongyi-MAI\/Z-Image/);
assert.match(view({...modelScope,project_model_visibility:{listed:[],not_listed:[]}},101).message,/Built-in models listed\nNone/);
assert.match(view(override,101).message,/official ModelScope address/);
console.log('clock offset, exact expiry, configuration invalidation, pending expiry and missing evidence passed');
"""
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script, str(ROOT / "app/www/assets/studio/features/providers/connection-evidence.js")], text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
