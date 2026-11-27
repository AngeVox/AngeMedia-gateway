"""助手设置表单的加载、并发和关闭后响应合同；焦点由真实浏览器验收。"""
import unittest

from test_web_studio_generate_image_operation_controls import run_studio_module_script
from test_web_studio_provider_channels import DOM


# 复用已有最小DOM夹具，不另建浏览器模拟器。
SETUP = DOM + r"""
Node.prototype.querySelectorAll=function(selector){return walk(this,n=>selector.split(',').includes(n.tagName));};
Node.prototype.querySelector=function(selector){return this.querySelectorAll(selector)[0];};
const toasts=new Node('div');document.getElementById=()=>toasts;
globalThis.setTimeout=()=>0;
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const response=data=>new Response(JSON.stringify(data),{headers:{'content-type':'application/json'}});
const config={assistant:{enabled:true,configured:true,llm_base_url:'https://fixture.invalid/v1',llm_model:'fixture-model'}};
const {createAssistantSettings}=await import('./studio/components/assistant-settings.js');
const {setLanguage}=await import('./studio/i18n.js');setLanguage('en-US');
const control=(view,label)=>walk(view.node,n=>n.tagName==='button'&&n.textContent===label)[0];
"""


class AssistantSettingsRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(SETUP + script + "\nconsole.log(JSON.stringify({ok:true}));", {})['ok'])

    def test_unknown_key_source_keeps_models_usable_and_exposes_a_localized_live_status(self):
        self.run_case(r"""
globalThis.fetch=async(_url,options)=>options.method==='GET'?response(config):
  response({data:['fixture-model','another-model'],key_source:'future-private-source'});
const view=createAssistantSettings();await flush();
await control(view,'Fetch Models').listeners.click();
const status=walk(view.node,n=>n.className.includes('assistant-settings-status'))[0];
assert.equal(status.role,'status');
assert.equal(status.ariaLive,'polite');
assert.ok(status.textContent.includes('2'));
assert.ok(!status.textContent.includes('future-private-source'));
assert.ok(!status.textContent.includes('assistantSettings.'));
assert.equal(view.node.querySelectorAll('select').length,1);
assert.equal(view.node.querySelectorAll('input').at(-1).value,'fixture-model');
""")

    def test_loading_disables_mutations_and_disposed_load_cannot_fill_old_form(self):
        self.run_case(r"""
let release;globalThis.fetch=()=>new Promise(resolve=>{release=resolve;});
const view=createAssistantSettings();
assert.equal(control(view,'Save Settings').disabled,true);
assert.equal(control(view,'Fetch Models').disabled,true);
view.dispose();release(response(config));await flush();
assert.equal(view.node.querySelectorAll('input')[1].value,'');
assert.equal(control(view,'Save Settings').disabled,true);
""")

    def test_failed_load_has_retry_and_never_enables_save_on_unknown_configuration(self):
        self.run_case(r"""
let reads=0;
globalThis.fetch=async()=>++reads===1?new Response('{}',{status:503}):response(config);
const view=createAssistantSettings();await flush();
assert.equal(control(view,'Save Settings').disabled,true);
assert.equal(control(view,'Reload settings').hidden,false);
await control(view,'Reload settings').listeners.click();await flush();
assert.equal(control(view,'Save Settings').disabled,false);
assert.equal(view.node.querySelectorAll('input')[1].value,config.assistant.llm_base_url);
assert.equal(reads,2);
view.dispose();
""")

    def test_models_request_serializes_actions_and_late_response_cannot_replace_selection(self):
        self.run_case(r"""
let release,calls=0;
globalThis.fetch=async(_url,options)=>{
  if(options.method==='GET')return response(config);
  calls++;return new Promise(resolve=>{release=resolve;});
};
const view=createAssistantSettings();await flush();
const inputs=view.node.querySelectorAll('input');inputs[2].value='unit-fixture-key';
const pending=control(view,'Fetch Models').listeners.click();
assert.equal(control(view,'Save Settings').disabled,true);
await control(view,'Test Connection').listeners.click();assert.equal(calls,1);
view.dispose();release(response({data:['late-model'],key_source:'form'}));await pending;
assert.equal(inputs[2].value,'');assert.equal(inputs.at(-1).value,'fixture-model');
assert.equal(view.node.querySelectorAll('select').length,0);
""")

    def test_accepted_save_refreshes_parent_after_return_without_toast_or_form_updates(self):
        self.run_case(r"""
let release,refreshes=0,payload;
globalThis.fetch=async(_url,options)=>{
  if(options.method==='GET')return response(config);
  payload=JSON.parse(options.body);return new Promise(resolve=>{release=resolve;});
};
const view=createAssistantSettings({onSaved:()=>{refreshes++;}});await flush();
const pending=control(view,'Save Settings').listeners.click();
view.dispose();release(response({status:'ok'}));await pending;
assert.equal(payload.settings.ANGE_LLM_MODEL,'fixture-model');
assert.equal(Object.hasOwn(payload.settings,'ANGE_LLM_API_KEY'),false);
assert.equal(refreshes,1);assert.equal(toasts.children.length,0);
""")

    def test_save_acknowledgement_clears_key_before_runtime_refresh_finishes(self):
        self.run_case(r"""
let refreshed;globalThis.fetch=async()=>response(config);
const view=createAssistantSettings({onSaved:()=>new Promise(resolve=>{refreshed=resolve;})});await flush();
const key=view.node.querySelectorAll('input')[2];key.value='unit-fixture-key';
const pending=control(view,'Save Settings').listeners.click();await flush();
assert.equal(key.value,'');assert.equal(control(view,'Save Settings').disabled,true);
view.dispose();refreshed();await pending;
""")

    def test_shared_focus_boundary_wraps_visible_controls_and_ignores_other_keys(self):
        self.run_case(r"""
const {containModalFocus}=await import('./studio/components/modal.js');
const focusable=()=>({tabIndex:0,getClientRects:()=>[{}],focus(){document.activeElement=this;}});
const first=focusable(),last=focusable(),disabled={...focusable(),disabled:true};
const root={querySelectorAll:()=>[first,disabled,last]};let prevented=0;
const event={key:'Tab',shiftKey:false,preventDefault(){prevented++;}};
last.focus();containModalFocus(root,event);assert.equal(document.activeElement,first);
event.shiftKey=true;containModalFocus(root,event);assert.equal(document.activeElement,last);
document.activeElement={};containModalFocus(root,event);assert.equal(document.activeElement,last);
event.key='Enter';containModalFocus(root,event);assert.equal(prevented,3);
""")
