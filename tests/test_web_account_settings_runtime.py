"""账户窗口、密码清理、会话归属与401分类的真实模块运行合同。"""
import unittest

from test_web_render_lifecycle import SETUP as VIEW_SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


SETUP = VIEW_SETUP + r"""
const {openAccountModal}=await import('./studio/components/account-settings.js?v=web-studio-2j');
const auth=await import('./studio/auth.js');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const fields=layer=>walk(layer,n=>n.tagName==='input');
const form=layer=>layer.querySelector('form');
const submit=layer=>form(layer).listeners.submit({preventDefault(){}});
const control=(layer,label)=>controls(layer).find(node=>node.textContent===label);
const close=layer=>control(layer,'Close').listeners.click();
const fill=layer=>{const nodes=fields(layer);nodes[1].value='  fixture-next  ';nodes[2].value='fixture-current';
 nodes[3].value='fixture-password-next';nodes[4].value='fixture-password-next';return nodes;};
const accepted={ok:true,requires_relogin:true,username:'fixture-next'};
"""


class AccountSettingsRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(SETUP + script + "\nconsole.log(JSON.stringify({ok:true}));", {})['ok'])

    def test_loading_is_immediate_singleton_and_cannot_save_before_confirmed_account(self):
        self.run_case(r"""
const read=deferred();let reads=0,writes=0;api.get=()=>{reads++;return read.promise;};api.patch=async()=>{writes++;};
const layer=openAccountModal();assert.equal(layer.isConnected,true);assert.equal(layer.querySelector('dialog').open,true);
assert.equal(openAccountModal(),layer);assert.equal(reads,1);assert.equal(control(layer,'Save account settings').disabled,true);
await submit(layer);assert.equal(writes,0);read.resolve({username:'fixture-current'});await flush();
assert.equal(fields(layer)[0].value,'fixture-current');assert.equal(control(layer,'Save account settings').disabled,false);close(layer);
""")

    def test_load_failure_has_explicit_retry_and_does_not_trust_cached_username(self):
        self.run_case(r"""
let reads=0,writes=0;api.get=async()=>{reads++;if(reads===1)throw new Error('private');return {username:'confirmed'};};api.patch=async()=>{writes++;};
const layer=openAccountModal();await flush();assert.equal(control(layer,'Reload account').hidden,false);
assert.equal(control(layer,'Save account settings').disabled,true);assert.ok(!layer.textContent.includes('private'));
await submit(layer);assert.equal(writes,0);await control(layer,'Reload account').listeners.click();
assert.equal(fields(layer)[0].value,'confirmed');assert.equal(reads,2);close(layer);
""")

    def test_route_pagehide_and_cancel_clear_passwords_and_ignore_late_load(self):
        self.run_case(r"""
for(const boundary of ['hashchange','pagehide','cancel']){
 const read=deferred();api.get=()=>read.promise;const layer=openAccountModal();const nodes=fill(layer);
 if(boundary==='cancel')layer.querySelector('dialog').listeners.cancel({preventDefault(){}});
 else [...listeners.get(boundary)].forEach(fn=>fn());
 assert.equal(layer.isConnected,false);assert.ok(nodes.every(n=>n.value===''));
 read.resolve({username:'late-private'});await flush();assert.ok(nodes.every(n=>n.value===''));
 assert.equal(document.body.style.overflow,'clip');
}
""")

    def test_validation_stays_local_and_keeps_inputs_until_a_request_is_sent(self):
        self.run_case(r"""
api.get=async()=>({username:'current'});let writes=0;api.patch=async()=>{writes++;};
const layer=openAccountModal();await flush();const nodes=fill(layer);nodes[4].value='different';await submit(layer);
assert.equal(writes,0);assert.equal(nodes[3].value,'fixture-password-next');assert.ok(layer.textContent.includes('New passwords do not match'));
nodes[1].value='';nodes[3].value='';nodes[4].value='';await submit(layer);assert.equal(writes,0);
nodes[1].value='next';nodes[2].value='';await submit(layer);assert.equal(writes,0);close(layer);
""")

    def test_combined_payload_is_unchanged_and_pending_save_cannot_be_duplicated_after_close(self):
        self.run_case(r"""
api.get=async()=>({username:'current'});const pending=deferred();let writes=0,payload;
api.patch=(_path,body)=>{writes++;payload=structuredClone(body);return pending.promise;};
const layer=openAccountModal();await flush();const nodes=fill(layer);const saving=submit(layer);
assert.deepEqual(payload,{current_password:'fixture-current',new_username:'fixture-next',new_password:'fixture-password-next',confirm_new_password:'fixture-password-next'});
assert.ok(nodes.slice(2).every(n=>n.value===''));assert.ok(nodes.slice(1).every(n=>n.disabled));await submit(layer);assert.equal(writes,1);
close(layer);assert.equal(openAccountModal(),undefined);assert.equal(writes,1);
pending.reject(new Error('unknown network failure'));await saving;
const current=openAccountModal();await flush();assert.ok(fields(current).slice(1).every(n=>n.value===''));close(current);
""")

    def test_wrong_current_password_stays_open_localizes_error_and_requires_reentry(self):
        self.run_case(r"""
api.get=async()=>({username:'current'});api.patch=async()=>{throw {safe:{code:'invalid_current_password',message:'旧中文消息'}};};
const layer=openAccountModal();await flush();const nodes=fill(layer);await submit(layer);
assert.equal(layer.isConnected,true);assert.equal(location.hash,'#/jobs');assert.ok(layer.textContent.includes('The current password is incorrect'));
assert.ok(nodes.slice(2).every(n=>n.value===''));assert.equal(nodes[1].value,'  fixture-next  ');
assert.equal(control(layer,'Save account settings').disabled,false);close(layer);
""")

    def test_accepted_update_invalidates_original_session_even_if_window_was_closed(self):
        self.run_case(r"""
for(const closed of [false,true]){
 location.hash='#/jobs';api.get=async()=>({username:'current'});const pending=deferred();api.patch=()=>pending.promise;
 const currentSession=auth.captureSessionScope(),layer=openAccountModal();await flush();fill(layer);const saving=submit(layer);
 if(closed)close(layer);pending.resolve(accepted);await saving;
 assert.equal(currentSession(),false);assert.equal(location.hash,'#/login');assert.equal(layer.isConnected,false);
}
""")

    def test_old_success_does_not_clear_new_login_or_unlock_another_pending_save(self):
        self.run_case(r"""
api.get=async()=>({username:'current'});const first=deferred();api.patch=()=>first.promise;
const old=openAccountModal();await flush();fill(old);const oldSaving=submit(old);close(old);
api.post=async()=>({username:'new-session'});await auth.login('fixture','fixture-only');const newSession=auth.captureSessionScope();
const next=openAccountModal();await flush();fill(next);const second=deferred();api.patch=()=>second.promise;const newSaving=submit(next);
first.resolve(accepted);await oldSaving;assert.equal(newSession(),true);assert.equal(location.hash,'#/jobs');assert.equal(next.isConnected,true);
close(next);assert.equal(openAccountModal(),undefined);second.reject(new Error('unknown'));await newSaving;
const last=openAccountModal();await flush();close(last);
""")

    def test_old_load_cannot_repopulate_window_for_a_new_session(self):
        self.run_case(r"""
const read=deferred();api.get=()=>read.promise;const old=openAccountModal();
api.post=async()=>({username:'new-session'});await auth.login('fixture','fixture-only');
api.get=async()=>({username:'new-session'});const next=openAccountModal();await flush();
read.resolve({username:'old-private'});await flush();assert.equal(old.isConnected,false);assert.equal(fields(next)[0].value,'new-session');close(next);
""")

    def test_malformed_success_is_unconfirmed_and_does_not_invalidate_session(self):
        self.run_case(r"""
api.get=async()=>({username:'current'});api.patch=async()=>({});const current=auth.captureSessionScope();
const layer=openAccountModal();await flush();fill(layer);await submit(layer);
assert.equal(current(),true);assert.equal(layer.isConnected,true);assert.ok(layer.textContent.includes('could not be confirmed'));close(layer);
""")


class AccountApiRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(VIEW_SETUP + script + "\nconsole.log(JSON.stringify({ok:true}));", {})['ok'])

    def test_password_challenge_code_does_not_expire_authenticated_session(self):
        self.run_case(r"""
const {setUnauthorizedHandler}=await import('./studio/api.js');let expired=0;setUnauthorizedHandler(()=>{expired++;});
globalThis.fetch=async()=>new Response(JSON.stringify({detail:{message:'当前密码错误',code:'invalid_current_password'}}),{status:401,headers:{'Content-Type':'application/json'}});
await assert.rejects(api.patch('/admin/account',{}),error=>error.status===401&&error.safe.code==='invalid_current_password');assert.equal(expired,0);
""")

    def test_normal_or_invalid_json_401_still_expires_session_but_login_failure_does_not(self):
        self.run_case(r"""
const {setUnauthorizedHandler}=await import('./studio/api.js');let expired=0;setUnauthorizedHandler(()=>{expired++;});
for(const body of [JSON.stringify({detail:'需要登录'}),'not JSON']){
 globalThis.fetch=async()=>new Response(body,{status:401,headers:{'Content-Type':'application/json'}});
 await assert.rejects(api.get('/admin/account'));assert.equal(expired,body==='not JSON'?2:1);
}
await assert.rejects(api.post('/admin/login',{},true));assert.equal(expired,2);
""")

    def test_stream_401_remains_an_authentication_failure(self):
        self.run_case(r"""
const {setUnauthorizedHandler}=await import('./studio/api.js');let expired=0;setUnauthorizedHandler(()=>{expired++;});
globalThis.fetch=async()=>new Response('{}',{status:401});await assert.rejects(api.stream('/fixture',{onEvent(){}}));assert.equal(expired,1);
""")

    def test_modal_close_hook_runs_once_and_cleanup_survives_hook_failure(self):
        self.run_case(r"""
const {createModal}=await import('./studio/components/modal.js?v=web-studio-2j');let calls=0;
const modal=createModal({title:'Fixture',onClose(){calls++;throw new Error('cleanup fixture');}});modal.show();
assert.throws(()=>modal.close());modal.close();assert.equal(calls,1);assert.equal(modal.overlay.isConnected,false);
assert.equal(document.body.style.overflow,'clip');assert.equal(listeners.get('pagehide').size,0);
""")
