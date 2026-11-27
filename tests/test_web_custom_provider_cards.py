"""自定义渠道卡片动作门、列表补读和结果归属回归。"""
import unittest

from test_web_provider_forms_runtime import SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


CARDS = SETUP + r"""
Node.prototype.replaceChildren=function(...nodes){
 for(const child of [...this.children])child.remove();
 this.append(...nodes);
};
const {renderCustomProviderSection}=await import('./studio/features/providers/custom-provider-section.js');
const snapshot=(id,enabled=true,updated_at='initial')=>({
 id,name:id,provider_type:'openai_image',configuration_complete:true,enabled,updated_at,
});
const show=()=>{const node=renderCustomProviderSection();content.append(node);return node;};
const cards=node=>walk(node,n=>hasClass(n,'provider-compact-card'));
const btn=(row,key)=>controls(row).find(n=>n.textContent===t(key));
const press=(row,key)=>btn(row,key).listeners.click({currentTarget:btn(row,key)});
const results=(row)=>walk(row,n=>hasClass(n,'provider-test-result'));
const dialog=()=>walk(document.body,n=>n.tagName==='dialog').at(-1);
"""


class CustomProviderCardsTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(
            CARDS + script + "\nconsole.log(JSON.stringify({ok:true}));", {}
        )["ok"])

    def test_same_channel_actions_are_mutually_exclusive_and_keep_siblings_available(self):
        self.run_case(r"""
let state=[snapshot('A'),snapshot('B')],calls=[];
const toggle=deferred(),probe=deferred();
api.get=async()=>({data:state});
api.post=(path,body)=>{calls.push({path,body});return path.endsWith('/enabled')?toggle.promise:probe.promise;};
const section=show();await flush();const [a,b]=cards(section);
const saving=press(a,'providers.disableAction');
await press(a,'providers.disableAction');await press(a,'providers.testAction');await press(a,'common.delete');
assert.ok(controls(a).every(button=>button.disabled));assert.equal(dialog(),undefined);
const testing=press(b,'providers.testAction');
state=[snapshot('A',false,'toggle'),snapshot('B')];toggle.resolve({ok:true,data:state[0]});await saving;await flush();
assert.equal(cards(section)[0],a);assert.equal(btn(a,'providers.enableAction').disabled,false);
assert.equal(btn(b,'providers.builtinTesting').disabled,true);
state[1]={...snapshot('B',true,'test'),last_test_status:'ok',last_response_ms:27};
probe.resolve({ok:true,status:'ok',model_found:true,elapsed_ms:27,data:state[1]});await testing;await flush();
assert.deepEqual(calls,[{path:'/admin/providers/A/enabled',body:{enabled:false}},{path:'/admin/providers/B/test',body:undefined}]);
assert.equal(results(b).length,1);assert.ok(results(b)[0].textContent.includes(t('providers.modelFoundYes')));
""")

    def test_two_reconciliations_keep_newer_snapshot_and_failed_read_requires_retry(self):
        self.run_case(r"""
const older=deferred(),newer=deferred();let reads=0;
const first=[snapshot('A'),snapshot('B')],last=[snapshot('A',false),snapshot('B',false)];
api.get=()=>{reads++;if(reads===1)return Promise.resolve({data:first}); if(reads===2)return older.promise;
 if(reads===3)return newer.promise;return Promise.resolve({data:last});};
api.post=async()=>({ok:true});const section=show();await flush();const [a,b]=cards(section);
await press(a,'providers.disableAction');await press(b,'providers.disableAction');
newer.reject(new Error('fixture read failure'));await flush();
older.resolve({data:[last[0],first[1]]});await flush();
assert.ok(btn(a,'providers.disableAction').disabled);assert.ok(btn(b,'providers.disableAction').disabled);
assert.equal(reads,3);await press(section,'common.refresh');assert.equal(reads,4);
assert.ok(!btn(a,'providers.enableAction').disabled);assert.ok(!btn(b,'providers.enableAction').disabled);
""")

    def test_remount_reconciles_from_list_without_reusing_detached_test_details(self):
        self.run_case(r"""
let reads=0,state=[snapshot('A')];const held=deferred();
api.get=async()=>{reads++;return {data:state};};api.post=()=>held.promise;
const old=show();await flush();const testing=press(cards(old)[0],'providers.testAction');
route('#/jobs');old.remove();route('#/providers');const current=show();await flush();
const a=cards(current)[0];assert.equal(btn(a,'providers.builtinTesting').disabled,true);
state=[{...snapshot('A',true,'latest'),last_test_status:'ok',last_response_ms:41,last_test_at:'2026-11-15T00:00:00Z'}];
held.resolve({ok:true,status:'ok',message:'old view transient detail',data:state[0]});await testing;await flush();
assert.equal(reads,3);assert.equal(toasts.children.length,0);assert.equal(results(a).length,1);
assert.ok(results(a)[0].textContent.includes('41ms'));assert.ok(!results(a)[0].textContent.includes('old view transient detail'));
assert.equal(btn(a,'providers.testAction').disabled,false);
""")

    def test_new_session_ignores_old_result_and_owns_its_own_lock(self):
        self.run_case(r"""
let reads=0;const first=deferred(),second=deferred();let sends=0;
api.get=async()=>{reads++;return {data:[snapshot('A')]};};api.post=()=>++sends===1?first.promise:second.promise;
const old=show();await flush();const pending=press(cards(old)[0],'providers.testAction');
auth.clearSession();old.remove();
const current=show();await flush();const a=cards(current)[0];const active=press(a,'providers.testAction');
first.resolve({ok:true,status:'ok',message:'old session detail'});await pending;await flush();
assert.equal(reads,2);assert.equal(btn(a,'providers.builtinTesting').disabled,true);assert.equal(toasts.children.length,0);
assert.equal(results(a).length,0);
second.reject(new Error('fixture test failure'));await active;await flush();
assert.equal(reads,3);assert.equal(btn(a,'providers.testAction').disabled,false);assert.equal(toasts.children.length,1);
""")

    def test_delete_confirm_can_close_while_accepted_delete_still_reconciles(self):
        self.run_case(r"""
let reads=0,sends=0,state=[snapshot('A')];const held=deferred();
api.get=async()=>{reads++;return {data:state};};
api.delete=path=>{assert.equal(path,'/admin/providers/A');sends++;return held.promise;};
const section=show();await flush();const a=cards(section)[0];
await press(a,'common.delete');const first=dialog();await press(a,'common.delete');
assert.equal(first,dialog());
const deleting=press(first,'common.delete');await press(first,'common.delete');
await press(first,'common.close');assert.equal(dialog(),undefined);
await press(a,'providers.testAction');assert.equal(sends,1);
state=[];held.resolve({ok:true});await deleting;await flush();
assert.equal(reads,2);assert.equal(cards(section).length,0);assert.equal(toasts.children.length,0);
""")
