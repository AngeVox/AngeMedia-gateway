"""内置渠道动作、快照读取、订阅释放和在途跨视图的实际模块测试。"""
import unittest

from test_web_provider_forms_runtime import SETUP as FORM_SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script

SETUP = FORM_SETUP+r"""
const create=document.createElement;
document.createElement=tag=>{const node=create(tag);node.classList.remove=name=>node.classList.toggle(name,false);return node;};
const {renderBuiltinConfigPanel}=await import('./studio/features/providers/builtin-config.js?v=provider-drawer-sections-1');
const {createBuiltinState}=await import('./studio/features/providers/builtin-state.js');
const {renderConnectionEvidence}=await import('./studio/features/providers/connection-evidence.js');
const evidence=(status='success')=>({status,evidence_state:'current',server_time:new Date().toISOString(),expires_at:new Date(Date.now()+60000).toISOString()});
const builtin=(id='alpha',extra={})=>({provider_id:id,display_name:id.toUpperCase(),provider_type:'openai_image',media_types:['image'],enabled:true,api_key_configured:true,base_url_override:'',connection_evidence:evidence(),...extra});
let configs=[builtin()];
api.get=async path=>path.includes('/provider-transport/')?transport:{data:configs};
const makePanel=(rows=configs)=>{const node=renderBuiltinConfigPanel(rows,reload,{models:[]});content.append(node);return node;};
const rowOf=(panel,id='alpha')=>walk(panel,n=>hasClass(n,'builtin-provider-row')&&n.dataset.providerId===id)[0];
const openBuiltin=async(panel,id='alpha')=>{await click(rowOf(panel,id),'providers.configureAction');await flush();return layer();};
const keyInput=view=>fields(view).find(n=>n.type==='password');
const baseInput=view=>fields(view).find(n=>n.type==='url');
const enabledInput=view=>fields(view).find(n=>n.type==='checkbox');
const statusOf=view=>walk(view,n=>!!n.dataset.status)[0];
"""


class BuiltinConfigRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(SETUP+script+"\nconsole.log(JSON.stringify({ok:true}));", {})['ok'])

    def test_save_preserves_payload_clears_key_and_locks_all_same_provider_actions(self):
        self.run_case(r"""
const panel=makePanel(),view=await openBuiltin(panel);keyInput(view).value='fixture-key';baseInput(view).value='https://fixture.invalid/v1';enabledInput(view).checked=false;
const held=deferred(),writes=[];api.post=(path,payload)=>{writes.push({path,payload:structuredClone(payload)});return held.promise;};
const pending=click(view,'providers.builtinSave');await click(view,'providers.builtinSave');await click(view,'providers.builtinClearKey');await click(rowOf(panel),'providers.builtinTestConnection');
assert.equal(writes.length,1);assert.deepEqual(writes[0],{path:'/admin/provider-configs/alpha',payload:{enabled:false,base_url_override:'https://fixture.invalid/v1',api_key:'fixture-key'}});
assert.equal(keyInput(view).value,'');assert.equal(baseInput(view).disabled,true);assert.equal(controls(rowOf(panel))[0].disabled,true);
configs=[builtin('alpha',{enabled:false,base_url_override:'https://fixture.invalid/v1'})];held.resolve({data:configs[0]});await pending;
assert.equal(enabledInput(view).disabled,false);assert.equal(reloads,0);assert.equal(rowOf(panel).querySelector('.builtin-provider-base').textContent.includes(t('providers.baseUrlOverridden')),true);closeProviderDrawer();
""")

    def test_clear_updates_row_and_open_drawer_key_metadata(self):
        self.run_case(r"""
const panel=makePanel(),view=await openBuiltin(panel);let request;
api.post=async(path,payload)=>{request={path,payload};configs=[builtin('alpha',{api_key_configured:false})];return {data:configs[0]};};
await click(view,'providers.builtinClearKey');assert.deepEqual(request,{path:'/admin/provider-configs/alpha/clear-key',payload:{}});
assert.ok(rowOf(panel).querySelector('.builtin-provider-key').textContent.includes(t('providers.notConfigured')));
assert.ok(view.textContent.includes(t('providers.notConfigured')));assert.equal(keyInput(view).value,'');closeProviderDrawer();
""")

    def test_failed_save_keeps_ordinary_draft_and_requires_key_reentry_without_automatic_retry(self):
        self.run_case(r"""
const view=await openBuiltin(makePanel());keyInput(view).value='fixture-key';baseInput(view).value='https://fixture.invalid/v1';
let writes=0;api.post=async()=>{writes++;throw new Error('fixture failed');};await click(view,'providers.builtinSave');
assert.equal(writes,1);assert.equal(keyInput(view).value,'');assert.equal(baseInput(view).value,'https://fixture.invalid/v1');assert.equal(baseInput(view).disabled,false);
assert.equal(view.querySelector('.form-error').hidden,false);closeProviderDrawer();
""")

    def test_closed_save_only_reconciles_live_list_and_next_open_reads_fresh_fields(self):
        self.run_case(r"""
const panel=makePanel(),view=await openBuiltin(panel);const held=deferred();api.post=()=>held.promise;
keyInput(view).value='fixture';enabledInput(view).checked=false;const pending=click(view,'providers.builtinSave');closeProviderDrawer();
await click(rowOf(panel),'providers.configureAction');assert.equal(layer(),undefined);const count=toasts.children.length;
configs=[builtin('alpha',{enabled:false,base_url_override:'https://new.fixture.invalid/v1'})];held.resolve({data:configs[0]});await pending;
assert.equal(toasts.children.length,count);assert.equal(reloads,0);assert.equal(statusOf(rowOf(panel)).dataset.status,'success');
const next=await openBuiltin(panel);assert.equal(Boolean(enabledInput(next).checked),false);assert.equal(baseInput(next).value,'https://new.fixture.invalid/v1');closeProviderDrawer();
""")

    def test_same_provider_row_and_drawer_test_cannot_overlap_but_other_provider_can(self):
        self.run_case(r"""
configs=[builtin(),builtin('beta')];const panel=makePanel(),view=await openBuiltin(panel);const held=deferred();let writes=0;
api.post=path=>{writes++;return path.includes('/alpha/')?held.promise:Promise.resolve({data:evidence('failed')});};
const first=click(view,'providers.builtinTestConnection');await click(rowOf(panel),'providers.builtinTesting');
await click(rowOf(panel,'beta'),'providers.builtinTestConnection');assert.equal(writes,2);
held.resolve({data:evidence()});await first;assert.equal(statusOf(rowOf(panel)).dataset.status,'success');closeProviderDrawer();
""")

    def test_acknowledged_save_is_not_reported_failed_when_followup_read_fails(self):
        self.run_case(r"""
const view=await openBuiltin(makePanel());api.post=async()=>({data:builtin()});api.get=async()=>{throw new Error('refresh failed');};
await click(view,'providers.builtinSave');assert.ok(toasts.textContent.includes(t('providers.builtinSaveSuccess')));
assert.equal(view.querySelector('.form-error').hidden,true);assert.equal(statusOf(view).dataset.status,'not_tested');closeProviderDrawer();
""")

    def test_secret_or_wrong_provider_response_is_not_accepted_or_rendered(self):
        self.run_case(r"""
for(const response of [{data:{api_key:'must-not-render'}},{data:builtin('other')}]){
 api.get=async path=>path.includes('provider-transport')?transport:{data:configs};const view=await openBuiltin(makePanel());
 api.post=async()=>response;await click(view,'providers.builtinSave');assert.ok(view.textContent.includes(t('providers.securityError')));
 assert.ok(!view.textContent.includes('must-not-render'));closeProviderDrawer();
}
""")

    def test_old_read_success_and_failure_cannot_override_new_snapshot(self):
        self.run_case(r"""
for(const failure of [false,true]){
 const state=createBuiltinState(configs,()=>true),first=deferred(),second=deferred();let calls=0;api.get=()=>++calls===1?first.promise:second.promise;
 const old=state.refresh('alpha'),next=state.refresh('alpha');second.resolve({data:[builtin('alpha',{enabled:false})]});assert.equal(await next,true);
 if(failure)first.reject(new Error());else first.resolve({data:[builtin('alpha',{enabled:true,connection_evidence:evidence('failed')})]});
 assert.equal(await old,null);assert.equal(state.get('alpha').enabled,false);assert.equal(state.get('alpha').connection_evidence.status,'success');state.dispose();
}
""")

    def test_starting_operation_invalidates_previous_read_before_it_returns(self):
        self.run_case(r"""
const state=createBuiltinState(configs,()=>true),read=deferred(),write=deferred();api.get=()=>read.promise;
const old=state.refresh('alpha');const pending=state.perform('alpha',()=>true,'save',async current=>{await write.promise;if(current())state.accept('alpha',builtin('alpha',{enabled:false}));});
read.resolve({data:[builtin('alpha',{enabled:true})]});assert.equal(await old,null);write.resolve();await pending;assert.equal(state.get('alpha').enabled,false);state.dispose();
""")

    def test_failed_read_downgrades_only_requested_provider_and_rejects_sensitive_snapshots(self):
        self.run_case(r"""
const state=createBuiltinState([builtin(),builtin('beta')],()=>true);api.get=async()=>({data:[builtin(),{...builtin('beta'),api_key:'must-not-render'}]});
assert.equal(await state.refresh('alpha'),false);assert.equal(state.get('alpha').connection_evidence.status,'not_tested');
assert.equal(state.get('beta').connection_evidence.status,'success');assert.ok(!JSON.stringify(state.get('beta')).includes('must-not-render'));state.dispose();
""")

    def test_disposed_old_panel_does_not_leave_new_panel_actions_locked(self):
        self.run_case(r"""
const first=createBuiltinState(configs,()=>true),held=deferred();let nextAction='';
const pending=first.perform('alpha',()=>true,'save',async()=>{await held.promise;});first.dispose();
const next=createBuiltinState(configs,()=>true);next.subscribe('alpha',(_snapshot,action)=>{nextAction=action;});assert.equal(nextAction,'save');
api.get=async()=>({data:[builtin('alpha',{enabled:false})]});held.resolve();await pending;await flush();
assert.equal(nextAction,null);assert.equal(next.get('alpha').enabled,false);next.dispose();
""")

    def test_old_session_completion_cannot_unlock_or_fill_new_session_operation(self):
        self.run_case(r"""
const old=createBuiltinState(configs,()=>true),first=deferred();const one=old.perform('alpha',()=>true,'save',async()=>first.promise);
auth.clearSession();const next=createBuiltinState(configs,()=>true),second=deferred();const two=next.perform('alpha',()=>true,'clear',async()=>second.promise);
first.resolve();await one;assert.equal(next.action('alpha'),'clear');second.resolve();await two;assert.equal(next.action('alpha'),null);old.dispose();next.dispose();
""")

    def test_unsubscribe_cancels_evidence_timer_and_never_extends_deadline(self):
        self.run_case(r"""
let timerId=0;const timers=new Map();globalThis.setTimeout=(fn,delay)=>{timers.set(++timerId,{fn,delay});return timerId;};globalThis.clearTimeout=id=>timers.delete(id);
const state=createBuiltinState(configs,()=>true),node=new Node('div');content.append(node);let renders=0;
const deadline=state.get('alpha').connection_evidence.deadline;
const release=state.subscribe('alpha',snapshot=>{renders++;renderConnectionEvidence(node,snapshot.connection_evidence);},{dispose:()=>{for(const id of [...timers.keys()])timers.delete(id);}});
assert.equal(state.get('alpha').connection_evidence.deadline,deadline);assert.equal(timers.size,1);release();release();assert.equal(timers.size,0);
state.accept('alpha',builtin('alpha',{enabled:false}));assert.equal(renders,1);state.dispose();
""")

    def test_closing_drawers_releases_status_subscription_and_timer(self):
        self.run_case(r"""
let timerId=0;const timers=new Map();globalThis.setTimeout=(fn,delay)=>{timers.set(++timerId,{fn,delay});return timerId;};globalThis.clearTimeout=id=>timers.delete(id);
const panel=makePanel();let last;
for(let n=0;n<3;n++){const view=await openBuiltin(panel);last=statusOf(view);closeProviderDrawer();last.dataset.status='closed';}
const count=()=>[...timers.values()].filter(item=>item.delay>10000).length;assert.equal(count(),1);
api.post=async()=>({data:evidence('failed')});configs=[builtin('alpha',{connection_evidence:evidence('failed')})];await click(rowOf(panel),'providers.builtinTestConnection');
assert.equal(last.dataset.status,'closed');assert.equal(count(),1);panel.remove();[...listeners.get('hashchange')].forEach(fn=>fn());assert.equal(count(),0);
""")

    def test_pause_drops_old_read_and_resume_reconciles_without_repeating_writes(self):
        self.run_case(r"""
const state=createBuiltinState(configs,()=>true),held=deferred();api.get=()=>held.promise;const reading=state.refresh('alpha');state.pause();
held.resolve({data:[builtin('alpha',{enabled:false})]});assert.equal(await reading,null);assert.equal(state.get('alpha').enabled,true);
let writes=0;api.post=async()=>{writes++;};api.get=async()=>({data:[builtin('alpha',{enabled:false})]});state.resume();await flush();
assert.equal(state.get('alpha').enabled,false);assert.equal(writes,0);state.dispose();
""")

    def test_open_requires_current_read_and_late_open_is_discarded_after_navigation(self):
        self.run_case(r"""
const panel=makePanel(),held=deferred();api.get=()=>held.promise;const opening=click(rowOf(panel),'providers.configureAction');route('#/jobs');held.resolve({data:configs});await opening;
assert.equal(layer(),undefined);assert.equal(toasts.children.length,0);
""")
