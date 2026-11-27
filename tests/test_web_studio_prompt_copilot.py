"""Web Studio Prompt Copilot source contracts."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from test_web_modal_runtime import SETUP as MODAL_SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "app" / "www" / "assets" / "studio"
IMAGE_PAGE = STUDIO / "features" / "generate-image" / "page.js"
VIDEO_PAGE = STUDIO / "features" / "generate-video" / "page.js"
COPILOT = STUDIO / "components" / "prompt-copilot.js"
I18N = STUDIO / "i18n.js"
ASSISTANT_CSS = STUDIO / "styles" / "assistant.css"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


# 最小DOM只验证请求与写入边界；原生焦点、键盘和布局另做真实浏览器验收。
COPILOT_SETUP = MODAL_SETUP + r"""
Node.prototype.querySelectorAll=function(selector){
  if(selector.startsWith('[data-'))return walk(this,n=>selector.includes('body')?n.dataset.promptCopilotBody:n.dataset.promptCopilotFooter);
  return walk(this,n=>selector.split(',').includes(n.tagName));
};
Node.prototype.dispatchEvent=function(){this.inputEvents=(this.inputEvents||0)+1;};
const toasts=new Node('div');document.getElementById=()=>toasts;
globalThis.setTimeout=()=>0;
const promptInput=new Node('textarea');promptInput.value='Original draft';document.body.append(promptInput);
const {api}=await import('./studio/api.js');
const {openPromptCopilot}=await import('./studio/components/prompt-copilot.js');
const result={model_prompt_en:'English suggestion',user_display_prompt:'English explanation',
 user_display_prompt_zh:'中文说明',notes:['English note'],notes_zh:['中文备注'],input_summary:{media_type:'image'}};
const flush=async()=>{await Promise.resolve();await Promise.resolve();};
const layer=()=>walk(document.body,n=>hasClass(n,'prompt-copilot-layer'))[0];
const action=(label)=>controls(layer()).find(n=>n.textContent===label);
"""


class PromptCopilotRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(
            COPILOT_SETUP + script + "\nconsole.log(JSON.stringify({ok:true}));", {}
        )["ok"])

    def test_retry_uses_original_snapshot_and_cannot_duplicate_pending_request(self):
        self.run_case(r"""
const calls=[];let release;
api.post=async(path,payload)=>{calls.push({path,payload});if(calls.length===1)throw new Error('private error');return new Promise(r=>{release=r;});};
openPromptCopilot({promptInput,mediaType:'image'});await flush();
assert.equal(calls.length,1);assert.ok(!layer().textContent.includes('private error'));
const retry=action('Try suggestion again');promptInput.value='Edited behind fixture';
const pending=retry.listeners.click();await retry.listeners.click();
openPromptCopilot({promptInput,mediaType:'image'});assert.equal(calls.length,2);
assert.deepEqual(calls[1],calls[0]);assert.equal(calls[1].payload.prompt,'Original draft');
release(result);await pending;assert.equal(promptInput.value,'Edited behind fixture');
assert.equal(layer().querySelector('[data-prompt-copilot-body]').textContent.includes('English explanation'),true);
""")

    def test_explicit_apply_uses_callback_once_and_reads_display_language_fields(self):
        self.run_case(r"""
api.post=async()=>result;let applied=0;
openPromptCopilot({promptInput,mediaType:'image',onApply:()=>{applied++;return false;}});await flush();
assert.equal(promptInput.value,'Original draft');assert.equal(applied,0);
assert.ok(layer().textContent.includes('English note'));assert.ok(!layer().textContent.includes('中文说明'));
const apply=action('Apply English prompt');apply.listeners.click();apply.listeners.click();
assert.equal(promptInput.value,'English suggestion');assert.equal(promptInput.inputEvents,1);assert.equal(applied,1);
assert.equal(layer(),undefined);assert.equal(document.activeElement,promptInput);
""")

    def test_route_close_drops_late_success_and_failure_without_touching_new_window(self):
        self.run_case(r"""
for(const rejectLate of [false,true]){
 let resolve,reject;api.post=()=>new Promise((a,b)=>{resolve=a;reject=b;});
 openPromptCopilot({promptInput,mediaType:'image'});const old=layer();
 [...listeners.get('hashchange')].forEach(fn=>fn());
 api.post=async()=>result;openPromptCopilot({promptInput,mediaType:'image'});await flush();
 const current=layer();if(rejectLate)reject(new Error('late private'));else resolve({...result,model_prompt_en:'Late suggestion'});
 await flush();assert.equal(old.isConnected,false);assert.equal(layer(),current);
 assert.ok(!current.textContent.includes('Late suggestion'));assert.equal(promptInput.value,'Original draft');
 action('Keep original').listeners.click();assert.equal(document.body.style.overflow,'clip');
}
""")

    def test_empty_success_is_retryable_and_detached_form_cannot_be_applied(self):
        self.run_case(r"""
api.post=async()=>({model_prompt_en:'   '});openPromptCopilot({promptInput,mediaType:'image'});await flush();
assert.ok(action('Try suggestion again'));assert.equal(action('Apply English prompt'),undefined);
api.post=async()=>result;await action('Try suggestion again').listeners.click();promptInput.remove();
action('Apply English prompt').listeners.click();assert.equal(promptInput.value,'Original draft');assert.ok(layer());
action('Keep original').listeners.click();
""")

    def test_chinese_legacy_fields_remain_compatible(self):
        self.run_case(r"""
setLanguage('zh-CN');api.post=async()=>({...result,user_display_prompt:undefined,notes:undefined});
openPromptCopilot({promptInput,mediaType:'image'});await flush();
assert.ok(layer().textContent.includes('中文说明'));assert.ok(layer().textContent.includes('中文备注'));
action('保留原提示词').listeners.click();assert.equal(promptInput.value,'Original draft');
""")

    def test_clipboard_completion_after_close_does_not_toast_on_the_new_page(self):
        self.run_case(r"""
let release,copied;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:text=>{
 copied=text;return new Promise(resolve=>{release=resolve;});
}}}});
api.post=async()=>result;openPromptCopilot({promptInput,mediaType:'image'});await flush();
const copy=action('Copy English prompt');
const pending=copy.listeners.click({currentTarget:copy});action('Keep original').listeners.click();
release();await pending;assert.equal(copied,'English suggestion');assert.equal(toasts.children.length,0);
assert.equal(promptInput.value,'Original draft');
""")


class WebStudioPromptCopilotContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.image = read(IMAGE_PAGE)
        cls.video = read(VIDEO_PAGE)
        cls.copilot = read(COPILOT)
        cls.i18n = read(I18N)
        cls.css = read(ASSISTANT_CSS)

    def _function_body(self, source: str, name: str) -> str:
        start = source.index(f"async function {name}")
        next_function = source.find("\n  function ", start + 1)
        next_async = source.find("\n  async function ", start + 1)
        candidates = [idx for idx in [next_function, next_async] if idx > start]
        end = min(candidates) if candidates else len(source)
        return source[start:end]

    def test_generate_image_prompt_copilot_is_real_entry_not_wip(self) -> None:
        self.assertIn("openPromptCopilot", self.image)
        self.assertIn("mediaType: 'image'", self.image)
        self.assertIn("onApply: applyPromptCopilotResult", self.image)
        self.assertIn("catalogProviderValue(model.provider_id)", self.image)
        self.assertIn("modelMatchesHint", self.image)
        self.assertNotIn("showWipFeature({ title: t('wip.promptCopilotTitle')", self.image)
        self.assertIn("'generateImage.promptCopilotAction': '提示词助手'", self.i18n)
        self.assertIn("'generateImage.promptCopilotAction': 'Prompt Copilot'", self.i18n)

    def test_generate_video_prompt_copilot_is_real_entry_not_wip(self) -> None:
        self.assertIn("openPromptCopilot", self.video)
        self.assertIn("mediaType: 'video'", self.video)
        self.assertIn("onApply: applyPromptCopilotResult", self.video)
        self.assertIn("modelMatchesHint", self.video)
        self.assertIn("applySizeSuggestion", self.video)
        self.assertNotIn("showWipFeature({ title: t('wip.promptCopilotTitle')", self.video)
        self.assertIn("generateVideo.promptCopilotAction", self.video)

    def test_copilot_calls_assistant_prompt_copilot_api(self) -> None:
        self.assertIn("api.post('/assistant/prompt-copilot'", self.copilot)
        self.assertNotIn("api.post('/prompt/enhance'", self.copilot)
        self.assertNotIn("/assistant/plan", self.copilot)
        self.assertNotIn("/assistant/generate", self.copilot)
        self.assertNotIn("fetch(", self.copilot)
        self.assertNotIn("setInterval", self.copilot)
        self.assertIn("onApply", self.copilot)
        self.assertIn("onApply(result)", self.copilot)
        self.assertIn("promptCopilot.timeline", self.copilot + self.i18n)
        self.assertIn("promptCopilot.timeline.local_prompt_enhancer", self.copilot + self.i18n)
        self.assertIn("promptCopilot.timeline.llm_prompt_copilot", self.copilot + self.i18n)
        self.assertIn("promptCopilot.timelineStatus.fallback", self.copilot + self.i18n)
        self.assertIn("promptCopilot.llmMode", self.copilot + self.i18n)
        self.assertIn("promptCopilot.fallbackMode", self.copilot + self.i18n)

    def test_generate_submit_does_not_auto_run_prompt_enhance(self) -> None:
        image_submit = self._function_body(self.image, "submitGeneration")
        video_submit = self._function_body(self.video, "submitVideo")
        for body in (image_submit, video_submit):
            with self.subTest(body=body[:40]):
                self.assertNotIn("/prompt/enhance", body)
                self.assertNotIn("openPromptCopilot", body)
                self.assertNotIn("model_prompt_en", body)

    def test_preview_apply_keep_cancel_semantics(self) -> None:
        for token in (
            "originalPrompt",
            "user_display_prompt_zh",
            "notes_zh",
            "model_prompt_en",
            "promptCopilot.applyEnglish",
            "promptCopilot.copyEnglish",
            "promptCopilot.keepOriginal",
            "promptCopilot.close",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.copilot + self.i18n)
        self.assertRegex(
            self.copilot,
            r"promptInput\.value\s*=\s*result\.model_prompt_en",
            "Prompt textarea should only be overwritten by the apply action.",
        )

    def test_no_raw_secret_or_provider_fields_rendered(self) -> None:
        for source in (self.copilot, self.image, self.video):
            with self.subTest(source=source[:30]):
                self.assertNotIn("request_hash", source)
                self.assertNotIn("input_json", source)
                self.assertNotIn("output_json", source)
                self.assertNotIn("provider_raw_body", source)
                self.assertNotIn("Authorization", source)

    def test_390px_layout_contract_for_copilot(self) -> None:
        self.assertIn(".prompt-copilot-modal", self.css)
        self.assertIn(".prompt-copilot-grid", self.css)
        mobile = re.search(r"@media \(max-width: 400px\)\s*\{(?P<body>.*?)\n\}", self.css, re.S)
        self.assertIsNotNone(mobile)
        body = mobile.group("body")
        self.assertIn(".prompt-copilot-modal", body)
        self.assertIn(".prompt-copilot-grid", body)


if __name__ == "__main__":
    unittest.main()
