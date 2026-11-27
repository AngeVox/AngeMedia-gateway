"""实际API和会话模块的乱序401运行合同，不连接服务端或真实账户。"""
import unittest

from test_web_studio_generate_image_operation_controls import run_studio_module_script


SETUP = r"""
import assert from 'node:assert/strict';
import {api, ApiError, setUnauthorizedHandler} from './studio/api.js';
import * as auth from './studio/auth.js';
globalThis.location={hash:'#/generate/image'};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
let expired=0;
setUnauthorizedHandler(()=>{expired++;auth.clearSession();location.hash='#/login';},auth.captureSessionScope);
const login=async(name='fixture-new')=>{
 const original=globalThis.fetch;globalThis.fetch=async()=>json({username:name});
 try{await auth.login(name,'fixture-only-password');}finally{globalThis.fetch=original;}
};
const rejected=p=>p.then(()=>{throw new Error('Request unexpectedly succeeded');},error=>{
 assert.ok(error instanceof ApiError);assert.equal(error.status,401);return error;
});
"""


class ApiSessionScopeRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        result = run_studio_module_script(SETUP + script + '\nconsole.log(JSON.stringify({ok:true}));', {})
        self.assertTrue(result['ok'])

    def test_all_ordinary_methods_keep_new_login_when_old_401_arrives(self):
        self.run_case(r"""
for(const method of ['get','post','patch','delete','upload']){
 const held=deferred();let options;globalThis.fetch=async(url,opts)=>{options=opts;return held.promise;};
 const body=method==='upload'?new FormData():{fixture:'only'};
 const result=rejected(api[method]('/fixture',body));await login(method);const current=auth.captureSessionScope();
 held.resolve(json({detail:'Old session expired'},401));await result;
 assert.equal(expired,0);assert.equal(current(),true);assert.equal((await auth.getSession()).username,method);
 assert.equal(location.hash,'#/generate/image');assert.equal(options.credentials,'include');
 assert.equal(options.method,{get:'GET',post:'POST',patch:'PATCH',delete:'DELETE',upload:'POST'}[method]);
 if(method==='upload'){assert.equal(options.body,body);assert.deepEqual(options.headers,{});}
 if(['post','patch'].includes(method))assert.equal(options.body,JSON.stringify(body));
}
""")

    def test_scope_is_checked_after_error_body_finishes_parsing(self):
        self.run_case(r"""
const body=deferred(),started=deferred();globalThis.fetch=async()=>({ok:false,status:401,statusText:'Unauthorized',
 headers:new Headers({'Content-Type':'application/json'}),json(){started.resolve();return body.promise;}});
const result=rejected(api.get('/fixture'));await started.promise;await login();const current=auth.captureSessionScope();
body.resolve({detail:{message:'old',code:'authentication_required'}});const error=await result;
assert.equal(error.safe.code,'authentication_required');assert.equal(expired,0);assert.equal(current(),true);
""")

    def test_late_non_json_and_invalid_json_401_keep_new_session(self):
        self.run_case(r"""
for(const type of ['text/html','application/json']){
 const held=deferred();globalThis.fetch=()=>held.promise;const result=rejected(api.get('/fixture'));
 await login();const current=auth.captureSessionScope();
 held.resolve(new Response('not JSON',{status:401,headers:{'Content-Type':type}}));await result;
 assert.equal(expired,0);assert.equal(current(),true);
}
""")

    def test_current_json_non_json_and_invalid_json_401_still_require_login(self):
        self.run_case(r"""
for(const response of [json({detail:'expired'},401),new Response('expired',{status:401}),
 new Response('not JSON',{status:401,headers:{'Content-Type':'application/json'}})]){
 await login();location.hash='#/generate/image';const before=expired,current=auth.captureSessionScope();
 globalThis.fetch=async()=>response;const error=await rejected(api.get('/fixture'));
 assert.equal(error.message,'Session expired');assert.equal(expired,before+1);assert.equal(current(),false);assert.equal(location.hash,'#/login');
}
""")

    def test_old_stream_header_401_keeps_new_session_and_preserves_signal(self):
        self.run_case(r"""
const held=deferred(),controller=new AbortController();let options;
globalThis.fetch=async(url,opts)=>{options=opts;return held.promise;};
const result=rejected(api.stream('/fixture',{signal:controller.signal,onEvent(){throw new Error('Unexpected frame');}}));
await login();const current=auth.captureSessionScope();held.resolve(json({},401));await result;
assert.equal(current(),true);assert.equal(expired,0);assert.equal(options.signal,controller.signal);
assert.equal(options.cache,'no-store');assert.equal(options.credentials,'include');
""")

    def test_current_stream_401_expires_but_abort_does_not(self):
        self.run_case(r"""
await login();globalThis.fetch=async()=>{throw new DOMException('Aborted','AbortError');};
await assert.rejects(api.stream('/fixture',{onEvent(){}}),error=>error.name==='AbortError');assert.equal(expired,0);
const current=auth.captureSessionScope();globalThis.fetch=async()=>json({},401);
await rejected(api.stream('/fixture',{onEvent(){}}));assert.equal(expired,1);assert.equal(current(),false);assert.equal(location.hash,'#/login');
""")

    def test_late_401_during_new_login_does_not_cancel_login_attempt(self):
        self.run_case(r"""
for(const kind of ['get','stream']){
 const old=deferred(),next=deferred();globalThis.fetch=async path=>path.endsWith('/admin/login')?next.promise:old.promise;
 const result=rejected(kind==='get'?api.get('/fixture'):api.stream('/fixture',{onEvent(){}}));
 const signingIn=auth.login('fixture-next','fixture-only-password'),pending=auth.captureSessionScope();
 old.resolve(json({},401));await result;assert.equal(expired,0);assert.equal(pending(),true);
 next.resolve(json({username:'fixture-next'}));await signingIn;assert.equal((await auth.getSession()).username,'fixture-next');
}
""")

    def test_login_401_remains_local_and_retains_safe_error(self):
        self.run_case(r"""
await login();const current=auth.captureSessionScope();globalThis.fetch=async()=>json({detail:{message:'bad login',code:'invalid_credentials'}},401);
const error=await rejected(api.post('/admin/login',{},true));assert.equal(error.safe.code,'invalid_credentials');
assert.equal(expired,0);assert.equal(current(),true);assert.equal(location.hash,'#/generate/image');
""")

    def test_password_challenge_retains_code_for_current_and_stale_scope(self):
        self.run_case(r"""
for(const stale of [false,true]){
 const held=deferred();globalThis.fetch=()=>held.promise;const result=rejected(api.patch('/admin/account',{}));
 if(stale)await login();const current=auth.captureSessionScope();
 held.resolve(json({detail:{message:'challenge',code:'invalid_current_password',private_key:'must be excluded'}},401));
 const error=await result;assert.equal(error.safe.code,'invalid_current_password');assert.equal(error.safe.private_key,undefined);
 assert.equal(expired,0);assert.equal(current(),true);
}
""")

    def test_parallel_401_expires_once_and_new_requests_still_expire(self):
        self.run_case(r"""
await login();const held=deferred();globalThis.fetch=()=>held.promise;
const results=[rejected(api.get('/fixture-one')),rejected(api.get('/fixture-two'))];
held.resolve(json({detail:'expired'},401));await Promise.all(results);assert.equal(expired,1);
await login();globalThis.fetch=async()=>json({detail:'new session expired'},401);
await rejected(api.get('/fixture-new'));assert.equal(expired,2);
""")

    def test_old_session_check_401_cannot_discard_new_login_cache(self):
        self.run_case(r"""
const held=deferred();globalThis.fetch=()=>held.promise;const checking=auth.getSession();await login();
const current=auth.captureSessionScope();held.resolve(json({detail:'expired'},401));
assert.equal((await checking).username,'fixture-new');assert.equal((await auth.getSession()).username,'fixture-new');
assert.equal(expired,0);assert.equal(current(),true);
""")

    def test_success_sse_delivery_and_unscoped_configuration_remain_compatible(self):
        self.run_case(r"""
globalThis.fetch=async()=>json({ok:true});assert.deepEqual(await api.get('/fixture'),{ok:true});
globalThis.fetch=async()=>new Response(null,{status:204});assert.equal(await api.delete('/fixture'),null);
const frames=[];globalThis.fetch=async()=>new Response('event: update\ndata: {"ok":true}\n\n',{headers:{'Content-Type':'text/event-stream'}});
await api.stream('/fixture',{onEvent:frame=>frames.push(frame)});assert.equal(frames.length,1);assert.equal(expired,0);
setUnauthorizedHandler(()=>{expired++;});globalThis.fetch=async()=>json({detail:'expired'},401);
await rejected(api.get('/fixture'));assert.equal(expired,1);
setUnauthorizedHandler(null);const error=await rejected(api.get('/fixture'));assert.equal(error.message,'expired');assert.equal(expired,1);
""")
