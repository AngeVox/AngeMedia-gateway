"""本页登录退出的真实模块发送顺序、缓存与表单生命周期。"""
import unittest

from test_web_render_lifecycle import SETUP as VIEW_SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


SETUP = r"""
import assert from 'node:assert/strict';
import {api} from './studio/api.js';
import * as auth from './studio/auth.js';
globalThis.location={hash:'#/jobs'};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const calls=[],pending=[];
const actualPost=api.post;
api.post=(path,body,login)=>{const held=deferred();calls.push({path,body,login});pending.push(held);return held.promise;};
"""


class AuthMutationRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(SETUP + script + '\nconsole.log(JSON.stringify({ok:true}));', {})['ok'])

    def test_overlapping_logins_send_in_order_and_only_current_result_commits(self):
        self.run_case(r"""
const first=auth.login('fixture-a','fixture-a-password'),second=auth.login('fixture-b','fixture-b-password');
assert.equal(calls.length,1);assert.deepEqual(calls[0],{path:'/admin/login',body:{username:'fixture-a',password:'fixture-a-password'},login:true});
pending[0].resolve({username:'fixture-a'});assert.equal(await first,null);await flush();assert.equal(calls.length,2);
assert.equal(calls[1].body.username,'fixture-b');pending[1].resolve({username:'fixture-b'});assert.equal((await second).username,'fixture-b');
assert.equal((await auth.getSession()).username,'fixture-b');
""")

    def test_logout_during_login_waits_for_cookie_and_cannot_restore_intermediate_session(self):
        self.run_case(r"""
let reads=0;api.get=async()=>{reads++;return {authenticated:true,username:'intermediate'};};
const signing=auth.login('fixture-a','fixture-a-password'),leaving=auth.logout();let checked=false;
const checking=auth.getSession().then(value=>{checked=true;return value;});await flush();assert.equal(calls.length,1);assert.equal(checked,false);
pending[0].resolve({username:'fixture-a'});assert.equal(await signing,null);await flush();assert.equal(calls[1].path,'/admin/logout');assert.equal(checked,false);
pending[1].resolve({ok:true});await leaving;assert.equal(await checking,null);assert.equal(reads,0);assert.equal(location.hash,'#/login');
""")

    def test_login_after_logout_waits_for_revocation_and_prevents_old_redirect(self):
        self.run_case(r"""
const leaving=auth.logout(),signing=auth.login('fixture-b','fixture-b-password');assert.equal(calls.length,1);
pending[0].resolve({ok:true});await leaving;await flush();assert.equal(location.hash,'#/jobs');assert.equal(calls[1].path,'/admin/login');
pending[1].resolve({username:'fixture-b'});await signing;assert.equal((await auth.getSession()).username,'fixture-b');
""")

    def test_superseded_queued_login_is_skipped_but_logout_is_not(self):
        self.run_case(r"""
const first=auth.login('fixture-a','fixture-a-password'),obsolete=auth.login('fixture-skip','fixture-skip-password');
const leaving=auth.logout(),last=auth.login('fixture-b','fixture-b-password');pending[0].resolve({username:'fixture-a'});
assert.equal(await first,null);assert.equal(await obsolete,null);await flush();assert.equal(calls.length,2);assert.equal(calls[1].path,'/admin/logout');
pending[1].resolve({ok:true});await leaving;await flush();assert.equal(calls.length,3);assert.equal(calls[2].body.username,'fixture-b');
pending[2].resolve({username:'fixture-b'});await last;assert.equal((await auth.getSession()).username,'fixture-b');
""")

    def test_superseded_failure_does_not_poison_later_login(self):
        self.run_case(r"""
const old=auth.login('fixture-a','fixture-a-password'),current=auth.login('fixture-b','fixture-b-password');
pending[0].reject(new Error('obsolete failure'));assert.equal(await old,null);await flush();
pending[1].resolve({username:'fixture-b'});await current;assert.equal((await auth.getSession()).username,'fixture-b');
""")

    def test_current_login_failure_does_not_restore_cookie_and_explicit_retry_succeeds(self):
        self.run_case(r"""
api.get=()=>{throw new Error('Failed login must not restore prior cookie');};
const first=auth.login('fixture-a','fixture-wrong'),rejected=assert.rejects(first,/current failure/);
pending[0].reject(new Error('current failure'));await rejected;assert.equal(await auth.getSession(),null);assert.equal(calls.length,1);
const retry=auth.login('fixture-b','fixture-b-password');pending[1].resolve({username:'fixture-b'});await retry;
assert.equal((await auth.getSession()).username,'fixture-b');
""")

    def test_logout_transport_failure_keeps_local_exit_and_releases_queue(self):
        self.run_case(r"""
api.get=()=>{throw new Error('Local exit must not restore a residual cookie');};
const leaving=auth.logout();pending[0].reject(new Error('transport unknown'));await leaving;
assert.equal(location.hash,'#/login');assert.equal(await auth.getSession(),null);
const signing=auth.login('fixture-b','fixture-b-password');pending[1].resolve({username:'fixture-b'});await signing;
assert.equal((await auth.getSession()).username,'fixture-b');assert.equal(calls.length,2);
""")

    def test_session_read_waits_for_latest_queued_mutation(self):
        self.run_case(r"""
let reads=0;api.get=async()=>{reads++;return {authenticated:true,username:'obsolete'};};
const first=auth.login('fixture-a','fixture-a-password');let done=false;const checking=auth.getSession().then(value=>{done=true;return value;});
const second=auth.login('fixture-b','fixture-b-password');pending[0].resolve({username:'fixture-a'});await first;await flush();assert.equal(done,false);
pending[1].resolve({username:'fixture-b'});await second;assert.equal((await checking).username,'fixture-b');assert.equal(reads,0);
""")


    def test_logout_http_401_cannot_cancel_new_login_queued_after_it(self):
        self.run_case(r"""
const {setUnauthorizedHandler}=await import('./studio/api.js');api.post=actualPost;let expired=0;
setUnauthorizedHandler(()=>{expired++;auth.clearSession();},auth.captureSessionScope);
const first=deferred(),response=deferred();let logins=0;
globalThis.fetch=async path=>path.endsWith('/logout')?response.promise:++logins===1?first.promise:
 new Response(JSON.stringify({username:'fixture-b'}),{headers:{'Content-Type':'application/json'}});
const obsolete=auth.login('fixture-a','fixture-a-password'),leaving=auth.logout(),signing=auth.login('fixture-b','fixture-b-password');
first.resolve(new Response(JSON.stringify({username:'fixture-a'}),{headers:{'Content-Type':'application/json'}}));
assert.equal(await obsolete,null);await flush();assert.equal(logins,1);
response.resolve(new Response('{}',{status:401,headers:{'Content-Type':'application/json'}}));await leaving;await signing;
assert.equal(expired,0);assert.equal((await auth.getSession()).username,'fixture-b');assert.equal(location.hash,'#/jobs');
""")


class LoginFormMutationRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(VIEW_SETUP + r"""
const auth=await import('./studio/auth.js');const {render}=await import('./studio/pages/login.js');
const fields=()=>walk(content,node=>node.tagName==='input');
const submit=()=>content.querySelector('form').listeners.submit({preventDefault(){}});
const submitButton=()=>controls(content).find(node=>node.type==='submit');
location.hash='#/login';
""" + script + '\nconsole.log(JSON.stringify({ok:true}));', {})['ok'])

    def test_superseded_success_does_not_navigate_and_password_clears_on_submit(self):
        self.run_case(r"""
const signing=deferred(),leaving=deferred();api.post=path=>path.endsWith('/login')?signing.promise:leaving.promise;await render();
fields()[0].value='fixture-a';fields()[1].value='fixture-password';const submission=submit();assert.equal(fields()[1].value,'');
assert.equal(submitButton().disabled,true);const logout=auth.logout();signing.resolve({username:'fixture-a'});await submission;
assert.equal(location.hash,'#/login');assert.equal(submitButton().disabled,false);leaving.resolve({ok:true});await logout;
""")

    def test_current_error_can_retry_without_duplicate_submission_or_old_password(self):
        self.run_case(r"""
const signing=deferred();let writes=0;api.post=()=>{writes++;return signing.promise;};await render();fields()[0].value='fixture-a';fields()[1].value='fixture-password';
const submission=submit();await submit();assert.equal(writes,1);signing.reject(new Error('fixture rejection'));await submission;
assert.equal(fields()[1].value,'');assert.equal(submitButton().disabled,false);assert.ok(content.textContent.includes('fixture rejection'));
api.post=async()=>({username:'fixture-a'});fields()[1].value='fixture-retry-password';await submit();assert.equal(location.hash,'#/dashboard');
""")
