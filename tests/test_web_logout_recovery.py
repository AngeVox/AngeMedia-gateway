"""退出确认、刷新恢复、认证超时与双语登录页的真实模块合同。"""
import unittest

from test_web_auth_mutations import SETUP as AUTH_SETUP
from test_web_render_lifecycle import SETUP as VIEW_SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


STORAGE = r"""
const saved=new Map();globalThis.sessionStorage={getItem:key=>saved.get(key)||null,
 setItem:(key,value)=>saved.set(key,String(value)),removeItem:key=>saved.delete(key)};
"""


class LogoutRecoveryRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(AUTH_SETUP + STORAGE + script + '\nconsole.log(JSON.stringify({ok:true}));', {})['ok'])

    def test_only_explicit_ack_confirms_logout_and_failure_can_retry(self):
        self.run_case(r"""
for(const result of [null,{}, {ok:false},{ok:'true'}]){
 api.post=async()=>result;assert.equal(await auth.logout(),false);assert.equal(auth.getLogoutRecovery().status,'unconfirmed');
 assert.equal(saved.get('studio_logout_recovery'),'unconfirmed');assert.equal(await auth.getSession(),null);
}
api.post=async()=>({ok:true});assert.equal(await auth.logout(),true);assert.equal(auth.getLogoutRecovery().status,'confirmed');
assert.equal(saved.get('studio_logout_recovery'),'confirmed');
""")

    def test_duplicate_exit_coalesces_and_snapshot_cannot_mutate_state(self):
        self.run_case(r"""
const first=auth.logout(),second=auth.logout();assert.equal(calls.length,1);assert.equal(saved.get('studio_logout_recovery'),'unconfirmed');
const snapshot=auth.getLogoutRecovery();assert.equal(snapshot.status,'pending');snapshot.status='confirmed';
assert.equal(auth.getLogoutRecovery().status,'pending');pending[0].resolve({ok:true});assert.deepEqual(await Promise.all([first,second]),[true,true]);
assert.equal(auth.getLogoutRecovery().completion,null);assert.deepEqual([...saved.values()],['confirmed']);
""")

    def test_auth_timeout_releases_queued_logout_and_signal_reaches_fetch(self):
        self.run_case(r"""
const timers=[],cleared=[];globalThis.setTimeout=(fn,ms)=>{timers.push({fn,ms});return timers.length;};globalThis.clearTimeout=id=>cleared.push(id);
api.post=actualPost;let requests=0;globalThis.fetch=(path,options)=>{
 requests++;assert.equal(options.credentials,'include');assert.ok(options.signal instanceof AbortSignal);
 if(path.endsWith('/logout'))return Promise.resolve(new Response('{"ok":true}',{headers:{'Content-Type':'application/json'}}));
 return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Timeout','AbortError'))));
};
const signing=auth.login('fixture-a','fixture-password'),leaving=auth.logout();assert.equal(requests,1);assert.equal(timers[0].ms,15000);
timers[0].fn();assert.equal(await signing,null);assert.equal(await leaving,true);assert.equal(requests,2);assert.equal(cleared.length,2);
""")

    def test_logout_timeout_is_unconfirmed_and_never_automatically_retries(self):
        self.run_case(r"""
let fire,requests=0;globalThis.setTimeout=(fn,ms)=>{assert.equal(ms,15000);fire=fn;return 1;};globalThis.clearTimeout=()=>{};
api.post=actualPost;globalThis.fetch=(path,options)=>{requests++;return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Timeout','AbortError'))));};
const leaving=auth.logout();fire();assert.equal(await leaving,false);assert.equal(requests,1);assert.equal(auth.getLogoutRecovery().status,'unconfirmed');
""")

    def test_reload_blocks_cookie_restore_until_retry_or_explicit_login(self):
        self.run_case(r"""
api.post=async()=>{throw new Error('transport unknown');};await auth.logout();
const reloaded=await import('./studio/auth.js?recovery-reload');api.get=()=>{throw new Error('Residual cookie must not restore');};
assert.equal(reloaded.getLogoutRecovery().status,'unconfirmed');assert.equal(await reloaded.getSession(),null);
api.post=async()=>({ok:true});assert.equal(await reloaded.logout(),true);
const confirmed=await import('./studio/auth.js?confirmed-reload');assert.equal(confirmed.getLogoutRecovery().status,'confirmed');assert.equal(await confirmed.getSession(),null);
api.post=async()=>({username:'fixture-next'});await confirmed.login('fixture-next','fixture-password');
assert.equal(confirmed.getLogoutRecovery().status,'idle');assert.equal(saved.size,0);assert.equal((await confirmed.getSession()).username,'fixture-next');
""")

    def test_storage_denial_does_not_break_current_document_exit_or_login(self):
        self.run_case(r"""
globalThis.sessionStorage={getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');},removeItem(){throw new Error('blocked');}};
const isolated=await import('./studio/auth.js?storage-blocked');api.post=async()=>({ok:true});assert.equal(await isolated.logout(),true);
assert.equal(isolated.getLogoutRecovery().status,'confirmed');api.post=async()=>({username:'fixture-next'});await isolated.login('fixture-next','fixture-password');
assert.equal(isolated.getLogoutRecovery().status,'idle');
""")

    def test_failed_login_keeps_recovery_marker_and_new_success_clears_it(self):
        self.run_case(r"""
api.post=async()=>{throw new Error('logout unknown');};await auth.logout();
await assert.rejects(auth.login('fixture-a','fixture-wrong'));assert.equal(auth.getLogoutRecovery().status,'unconfirmed');
api.post=async()=>({username:'fixture-b'});await auth.login('fixture-b','fixture-password');assert.equal(auth.getLogoutRecovery().status,'idle');assert.equal(saved.size,0);
""")

    def test_old_exit_cannot_overwrite_new_attempt_or_successful_login(self):
        self.run_case(r"""
const old=auth.logout(),obsolete=auth.login('fixture-skip','fixture-password'),current=auth.logout();pending[0].reject(new Error('old failed'));
assert.equal(await old,false);assert.equal(await obsolete,null);await flush();assert.equal(auth.getLogoutRecovery().status,'pending');
pending[1].resolve({ok:true});assert.equal(await current,true);const signing=auth.login('fixture-b','fixture-password');pending[2].resolve({username:'fixture-b'});await signing;
assert.equal(auth.getLogoutRecovery().status,'idle');assert.equal(saved.size,0);
""")


class LogoutRecoveryPageRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(VIEW_SETUP + r"""
const auth=await import('./studio/auth.js'),{render}=await import('./studio/pages/login.js');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const retry=()=>controls(content).find(node=>['Retry sign-out','重试退出'].includes(node.textContent));
const primary=()=>controls(content).find(node=>node.type==='submit');
location.hash='#/login';
""" + script + '\nconsole.log(JSON.stringify({ok:true}));', {})['ok'])

    def test_bilingual_failure_retry_confirmation_and_busy_exclusion(self):
        self.run_case(r"""
for(const lang of ['zh-CN','en-US']){
 setLanguage(lang);api.post=async()=>{throw new Error('private failure');};await auth.logout();await render();
 assert.equal(retry().hidden,false);assert.equal(retry().type,'button');assert.ok(!content.textContent.includes('private failure'));
 const held=deferred();let writes=0;api.post=()=>{writes++;return held.promise;};
 const retrying=retry().listeners.click();assert.equal(primary().disabled,true);await retry().listeners.click();
 await content.querySelector('form').listeners.submit({preventDefault(){}});assert.equal(writes,1);
 held.resolve({ok:true});await retrying;assert.equal(retry().hidden,true);assert.equal(primary().disabled,false);
 assert.ok(content.textContent.includes(lang==='zh-CN'?'这次登录已结束':'You have been signed out'));
}
""")

    def test_pending_completion_updates_current_page_without_subscription(self):
        self.run_case(r"""
const held=deferred();api.post=()=>held.promise;const leaving=auth.logout();await render();
assert.ok(content.textContent.includes('Signing out'));held.reject(new Error('unknown'));await leaving;await flush();assert.equal(retry().hidden,false);
assert.equal(listeners.get('hashchange')?.size||0,0);assert.equal(listeners.get('pagehide')?.size||0,0);
""")

    def test_late_retry_after_route_change_does_not_update_old_page(self):
        self.run_case(r"""
api.post=async()=>{throw new Error('unknown');};await auth.logout();router.start();await render();const old=content.querySelector('.login-recovery');
const held=deferred();api.post=()=>held.promise;const retrying=retry().listeners.click();const text=old.textContent;
route('#/jobs');content.textContent='Current page';held.resolve({ok:true});await retrying;assert.equal(old.textContent,text);assert.equal(content.textContent,'Current page');
""")

    def test_failed_retry_remains_visible_and_explicit_login_clears_recovery(self):
        self.run_case(r"""
api.post=async()=>{throw new Error('unknown');};await auth.logout();await render();await retry().listeners.click();
assert.equal(retry().hidden,false);assert.equal(retry().disabled,false);api.post=async()=>({username:'fixture-next'});
const fields=walk(content,node=>node.tagName==='input');fields[0].value='fixture-next';fields[1].value='fixture-password';
await content.querySelector('form').listeners.submit({preventDefault(){}});assert.equal(location.hash,'#/dashboard');assert.equal(auth.getLogoutRecovery().status,'idle');
""")
