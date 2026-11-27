"""渠道抽屉、表单在途归属和只写输入的实际JS模块测试。"""
import unittest

from test_web_render_lifecycle import SETUP as VIEW_SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script

SETUP = VIEW_SETUP + r"""
location.hash='#/providers';
document.documentElement=new Node('html');document.documentElement.lang='en-US';
document.addEventListener=(...args)=>window.addEventListener(...args);
document.removeEventListener=(...args)=>window.removeEventListener(...args);
Node.prototype.classListRemove=function(name){this.classList.toggle(name,false);};
const queryNodes=Node.prototype.querySelectorAll;
Node.prototype.querySelectorAll=function(selector){
 if(selector==='input[type="password"]')return walk(this,n=>n.tagName==='input'&&n.type==='password');
 return queryNodes.call(this,selector);
};
const {t}=await import('./studio/i18n.js');
const auth=await import('./studio/auth.js');
const {openCreateProvider,openEditProvider}=await import('./studio/features/providers/provider-form.js?v=provider-drawer-sections-1');
const {openProviderDrawer,closeProviderDrawer}=await import('./studio/features/providers/provider-drawer.js?v=provider-drawer-sections-1');
const {providerTransportSection}=await import('./studio/features/providers/provider-transport.js?v=provider-connectivity-1');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const layer=()=>walk(document.body,n=>hasClass(n,'provider-config-drawer-layer')).at(-1);
const fields=view=>walk(view,n=>n.tagName==='input');
const fieldBy=(view,name)=>fields(view).find(n=>n.name===name);
const click=(view,key)=>controls(view).find(n=>n.textContent===t(key)).listeners.click();
const fill=view=>{for(const [name,value] of Object.entries({name:'Fixture channel',base_url:'https://fixture.invalid/v1',default_model:'fixture-model',api_key:'fixture-key-only'}))fieldBy(view,name).value=value;fieldBy(view,'enabled').checked=true;};
const fixture=id=>({data:{id,name:id,provider_type:'openai_image',base_url:'https://fixture.invalid/v1',default_model:'fixture-model',editable:true,enabled:true}});
const transport={data:{transport_mode:'direct',effective_mode:'direct',effective_source:'provider',proxy_configured:false}};
const triggerA=new Node('button'),triggerB=new Node('button');document.body.append(triggerA,triggerB);
let reloads=0;
const reload=()=>{reloads++;};
"""


class ProviderFormsRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(SETUP+script+"\nconsole.log(JSON.stringify({ok:true}));", {})['ok'])

    def test_drawer_uses_native_modal_and_clears_secrets_on_close_and_pagehide(self):
        self.run_case(r"""
for(const boundary of ['close','pagehide','hashchange','cancel']){
 location.hash='#/providers';openCreateProvider(reload,triggerA);const old=layer();fill(old);
 assert.equal(old.querySelector('dialog').open,true);assert.equal(document.body.style.overflow,'hidden');
 if(boundary==='close')closeProviderDrawer();else if(boundary==='cancel')old.querySelector('dialog').listeners.cancel({preventDefault(){}});
 else [...listeners.get(boundary)].forEach(fn=>fn({persisted:true}));
 assert.equal(old.isConnected,false);assert.equal(fieldBy(old,'api_key').value,'');assert.equal(document.body.style.overflow,'clip');
}
""")

    def test_edit_read_cannot_open_after_navigation_or_another_open_intent(self):
        self.run_case(r"""
for(const next of ['route','create','edit']){
 location.hash='#/providers';const held=deferred();api.get=path=>path.includes('provider-transport')?Promise.resolve(transport):held.promise;
 const old=openEditProvider({id:'old'},triggerA);
 if(next==='route')route('#/jobs');
 if(next==='create')openCreateProvider(reload,triggerB);
 if(next==='edit'){api.get=async path=>path.includes('provider-transport')?transport:fixture('current');await openEditProvider({id:'current'},triggerB);}
 held.resolve(fixture('old'));await old;await flush();
 if(next==='route')assert.equal(layer(),undefined);else assert.ok(!layer().textContent.includes('old · openai_image'));
 closeProviderDrawer();
}
""")

    def test_late_detail_failure_does_not_toast_after_leaving(self):
        self.run_case(r"""
const held=deferred();api.get=()=>held.promise;const pending=openEditProvider({id:'old'},triggerA);
route('#/jobs');held.reject(new Error('late'));await pending;assert.equal(toasts.children.length,0);
""")

    def test_create_payload_and_capabilities_are_preserved_and_duplicate_submission_is_blocked(self):
        self.run_case(r"""
const held=deferred(),writes=[];api.post=(path,payload)=>{writes.push({path,payload:structuredClone(payload)});return held.promise;};
openCreateProvider(reload,triggerA);const old=layer();fill(old);
fieldBy(old,'capability_image_edit').checked=true;fieldBy(old,'capability_image_edit').listeners.change();
fieldBy(old,'capability_max_reference_images').value='4';fieldBy(old,'capability_mask').checked=true;
const pending=click(old,'providers.createSubmit');await click(old,'providers.creating');
assert.equal(writes.length,1);assert.equal(fieldBy(old,'api_key').value,'');assert.ok(fields(old).every(n=>n.disabled));
assert.deepEqual(writes[0],{path:'/admin/providers',payload:{name:'Fixture channel',base_url:'https://fixture.invalid/v1',default_model:'fixture-model',api_key:'fixture-key-only',enabled:true,provider_type:'openai_image',capabilities:{text_to_image:true,image_edit:true,max_reference_images:4,supports_mask:true}}});
held.resolve({data:{id:'created'}});await pending;assert.equal(reloads,1);assert.equal(old.isConnected,false);
""")

    def test_close_during_save_blocks_reopen_until_settlement_and_drops_late_success(self):
        self.run_case(r"""
const held=deferred();let writes=0;api.post=()=>{writes++;return held.promise;};
openCreateProvider(reload,triggerA);const old=layer();fill(old);const pending=click(old,'providers.createSubmit');closeProviderDrawer();
openCreateProvider(reload,triggerA);assert.equal(layer(),undefined);const count=toasts.children.length;
held.resolve({data:{id:'created'}});await pending;assert.equal(reloads,0);assert.equal(toasts.children.length,count);assert.equal(writes,1);
openCreateProvider(reload,triggerA);assert.ok(layer());closeProviderDrawer();
""")

    def test_failed_save_keeps_normal_draft_requires_secret_reentry_and_never_auto_retries(self):
        self.run_case(r"""
let writes=0;api.post=async()=>{writes++;throw new Error('unconfirmed');};openCreateProvider(reload,triggerA);const view=layer();fill(view);
await click(view,'providers.createSubmit');assert.equal(writes,1);assert.equal(view.isConnected,true);assert.equal(fieldBy(view,'name').value,'Fixture channel');
assert.equal(fieldBy(view,'api_key').value,'');assert.equal(fieldBy(view,'name').disabled,undefined);
assert.ok(view.textContent.includes(t('providers.createError')));closeProviderDrawer();
""")

    def test_secret_response_and_old_session_do_not_leak_or_reload(self):
        self.run_case(r"""
api.post=async()=>({data:{api_key:'must-not-render'}});openCreateProvider(reload,triggerA);fill(layer());await click(layer(),'providers.createSubmit');
assert.equal(reloads,0);assert.ok(layer().textContent.includes(t('providers.securityError')));assert.ok(!layer().textContent.includes('must-not-render'));closeProviderDrawer();
const held=deferred();api.post=()=>held.promise;openCreateProvider(reload,triggerA);fill(layer());const old=layer(),pending=click(old,'providers.createSubmit');
auth.clearSession();held.resolve({data:{id:'late'}});await pending;assert.equal(reloads,0);assert.equal(old.isConnected,false);
""")

    def test_new_session_pending_save_is_not_unlocked_by_old_completion(self):
        self.run_case(r"""
const first=deferred(),second=deferred();api.post=()=>first.promise;openCreateProvider(reload,triggerA);fill(layer());const old=click(layer(),'providers.createSubmit');closeProviderDrawer();
auth.clearSession();api.post=()=>second.promise;openCreateProvider(reload,triggerA);fill(layer());const current=layer(),next=click(current,'providers.createSubmit');
first.resolve({data:{id:'old'}});await old;assert.equal(reloads,0);assert.equal(controls(current).at(-1).disabled,true);
closeProviderDrawer();openCreateProvider(reload,triggerA);assert.equal(layer(),undefined);second.reject(new Error());await next;
""")

    def test_edit_payload_uses_patch_and_preserves_notes_and_empty_secret(self):
        self.run_case(r"""
api.get=async path=>path.includes('provider-transport')?transport:fixture('edit/id');let saved;
api.patch=async(path,payload)=>{saved={path,payload};return {data:{id:'edit/id'}};};
const {customProviderActions}=await import('./studio/features/providers/provider-action-gate.js');
const changes=[];customProviderActions.subscribe((id,reconcile)=>{if(reconcile)changes.push(id);});
await openEditProvider({id:'edit/id'},triggerA);await flush();const view=layer();
fieldBy(view,'api_key').value='';view.querySelector('textarea').value='Notes';await click(view,'providers.editSubmit');
assert.equal(saved.path,'/admin/providers/edit%2Fid');assert.equal(saved.payload.notes,'Notes');assert.equal(saved.payload.api_key,'');
assert.ok(!('provider_type' in saved.payload));assert.deepEqual(changes,['edit/id']);
""")

    def test_transport_load_blocks_save_and_late_read_cannot_fill_closed_section(self):
        self.run_case(r"""
const held=deferred();api.get=()=>held.promise;let writes=0;api.post=async()=>{writes++;};
const section=providerTransportSection('fixture');content.append(section);await flush();
assert.equal(controls(section)[0].disabled,true);await click(section,'providers.transportSave');assert.equal(writes,0);
const before=section.textContent;section.remove();held.resolve(transport);await flush();assert.equal(section.textContent,before);
""")

    def test_transport_load_failure_has_explicit_retry_in_both_languages(self):
        self.run_case(r"""
for(const language of ['zh-CN','en-US']){
 setLanguage(language);let reads=0;api.get=async()=>{if(++reads===1)throw new Error();return transport;};
 const section=providerTransportSection('fixture');content.append(section);await flush();
 assert.equal(controls(section)[0].disabled,true);assert.equal(controls(section).at(-1).hidden,false);
 await click(section,'common.refresh');assert.equal(reads,2);assert.equal(controls(section)[0].disabled,false);section.remove();
}
""")

    def test_transport_save_has_original_payload_and_no_duplicate_or_late_feedback(self):
        self.run_case(r"""
api.get=async()=>transport;const held=deferred();let writes=0,saved;api.post=(path,payload)=>{writes++;saved={path,payload};return held.promise;};
const section=providerTransportSection('fixture/id');content.append(section);await flush();
section.querySelector('select').value='explicit_proxy';section.querySelector('select').listeners.change();section.querySelector('input').value='http://fixture.invalid:7890';
const pending=click(section,'providers.transportSave');await click(section,'providers.transportSave');assert.equal(writes,1);
assert.deepEqual(saved,{path:'/admin/provider-transport/fixture%2Fid',payload:{transport_mode:'explicit_proxy',proxy_url:'http://fixture.invalid:7890'}});
assert.equal(section.querySelector('input').value,'');section.remove();const count=toasts.children.length;held.resolve(transport);await pending;assert.equal(toasts.children.length,count);
""")

    def test_reopened_transport_waits_for_previous_save_then_requires_fresh_read(self):
        self.run_case(r"""
let reads=0;api.get=async()=>{reads++;return transport;};const held=deferred();api.post=()=>held.promise;
const old=providerTransportSection('fixture');content.append(old);await flush();const pending=click(old,'providers.transportSave');old.remove();
const next=providerTransportSection('fixture');content.append(next);await flush();assert.equal(reads,1);assert.equal(controls(next)[0].disabled,true);
held.resolve(transport);await pending;await click(next,'common.refresh');assert.equal(reads,2);assert.equal(controls(next)[0].disabled,false);next.remove();
""")
