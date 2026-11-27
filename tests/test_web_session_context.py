"""跨标签提示、会话标记、身份复核与资源恢复门的实际模块合同。"""
import unittest

from test_web_render_lifecycle import SETUP as VIEW_SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


SETUP = VIEW_SETUP + r"""
const auth=await import('./studio/auth.js');
const {setSessionContextProvider,setUnauthorizedHandler}=await import('./studio/api.js');
const {t}=await import('./studio/i18n.js');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
const sign=async(context='context-a',name='fixture-a')=>{const post=api.post;api.post=async()=>({username:name,session_context:context});
 try{await auth.login(name,'fixture-only-password');}finally{api.post=post;}};
const wire=()=>setSessionContextProvider(auth.getSessionContext,auth.waitForSessionReady);
const same=()=>({authenticated:true,username:'fixture-a',session_context:'context-a'});
let expired=0;setUnauthorizedHandler(()=>{expired++;auth.clearSession();location.hash='#/login';},auth.captureSessionScope);
const changed={detail:{code:'session_context_changed',message:'Server detail'}};
"""


class WebSessionContextRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(SETUP + script + '\nconsole.log(JSON.stringify({ok:true}));', {})['ok'])

    def test_all_http_methods_and_stream_attach_original_context(self):
        self.run_case(r"""
await sign();wire();const calls=[];globalThis.fetch=async(url,options)=>{calls.push({url,options});return json({ok:true});};
for(const method of ['get','post','patch','delete','upload'])await api[method]('/fixture',method==='upload'?new FormData():{fixture:true});
globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(null,{status:204});};await api.stream('/fixture',{onEvent(){}});
assert.equal(calls.length,6);assert.ok(calls.every(row=>row.options.headers['X-Studio-Session-Context']==='context-a'));
assert.ok(calls.every(row=>row.options.credentials==='include'));assert.equal(calls[4].options.headers['Content-Type'],undefined);
assert.equal(api.eventUrl('/admin/jobs/fixture/stream'),'/v1/admin/jobs/fixture/stream?studio_session_context=context-a');
""")

    def test_missing_context_blocks_business_but_not_session_bootstrap(self):
        self.run_case(r"""
wire();let calls=0;globalThis.fetch=async()=>{calls++;return json(same());};
await assert.rejects(api.post('/admin/jobs/images',{}),error=>error.safe.code==='session_context_changed');assert.equal(calls,0);
await api.get('/admin/session');assert.equal(calls,1);assert.throws(()=>api.eventUrl('/admin/jobs/fixture/stream'));
await assert.rejects(api.post('/admin/logout',undefined,true),error=>error.safe.code==='session_context_changed');assert.equal(calls,1);
""")

    def test_late_context_409_cannot_clear_new_login_for_http_or_stream(self):
        self.run_case(r"""
for(const stream of [false,true]){
 await sign();wire();const held=deferred();globalThis.fetch=()=>held.promise;
 const pending=stream?api.stream('/fixture',{onEvent(){}}):api.get('/fixture');const rejected=assert.rejects(pending,error=>error.status===409);
 await flush();await sign('context-b','fixture-b');held.resolve(json(changed,409));await rejected;
 assert.equal(expired,0);assert.equal((await auth.getSession()).username,'fixture-b');
}
""")

    def test_current_context_409_invalidates_and_translates_both_transport_paths(self):
        self.run_case(r"""
for(const lang of ['zh-CN','en-US'])for(const stream of [false,true]){
 setLanguage(lang);await sign();wire();const before=expired;globalThis.fetch=async()=>json(changed,409);
 await assert.rejects(stream?api.stream('/fixture',{onEvent(){}}):api.get('/fixture'),error=>error.message===t('auth.sessionChanged'));
 assert.equal(expired,before+1);assert.equal(auth.getSessionContext(),null);
}
""")

    def test_verification_shares_read_and_keeps_same_session_epoch(self):
        self.run_case(r"""
await sign();const scope=auth.captureSessionScope(),held=deferred();let reads=0,requestOptions;
api.get=(_,opts)=>{reads++;requestOptions=opts;return held.promise;};const a=auth.getSession({refresh:true}),b=auth.getSession({refresh:true});
assert.equal(reads,1);assert.equal(requestOptions.cache,'no-store');assert.ok(requestOptions.signal instanceof AbortSignal);
held.resolve(same());await a;await b;assert.equal(scope(),true);assert.equal(auth.getSessionContext(),'context-a');
""")

    def test_business_waits_for_verification_and_mismatch_prevents_dispatch(self):
        self.run_case(r"""
for(const mismatch of [false,true]){
 await sign();wire();const held=deferred();let posts=0;
 globalThis.fetch=async(url)=>url.endsWith('/session')?held.promise:(posts++,json({ok:true}));
 const check=auth.getSession({refresh:true}),writing=api.post('/admin/jobs/images',{}),rejected=mismatch?assert.rejects(writing):writing;
 await flush();assert.equal(posts,0);held.resolve(json(mismatch?{...same(),session_context:'different'}:same()));
 await check;await rejected;assert.equal(posts,mismatch?0:1);
}
""")

    def test_failed_check_preserves_cached_fact_but_blocks_writes_until_explicit_retry(self):
        self.run_case(r"""
await sign();wire();globalThis.fetch=async()=>{throw new Error('controlled offline');};await assert.rejects(auth.getSession({refresh:true}));
assert.equal(auth.getSessionContext(),'context-a');let posts=0;globalThis.fetch=async url=>url.endsWith('/session')?json(same()):(posts++,json({ok:true}));
await assert.rejects(api.post('/admin/jobs/images',{}));assert.equal(posts,0);
await auth.getSession({refresh:true});await api.post('/admin/jobs/images',{});assert.equal(posts,1);
""")

    def test_old_verification_cannot_overwrite_or_poison_new_local_login(self):
        self.run_case(r"""
await sign();const held=deferred();api.get=()=>held.promise;const old=auth.getSession({refresh:true}),rejected=assert.rejects(old,error=>error.name==='AbortError');
await sign('context-b','fixture-b');held.resolve(same());await rejected;await auth.waitForSessionReady();
assert.equal((await auth.getSession()).username,'fixture-b');assert.equal(auth.getSessionContext(),'context-b');
""")

    def test_notice_invalidates_old_callbacks_before_storage_event_and_contains_no_identity(self):
        self.run_case(r"""
await sign();const scope=auth.captureSessionScope(),notice=auth.readSessionNotice();assert.ok(notice);
assert.ok(!notice.includes('fixture-a')&&!notice.includes('context-a'));
localStorage.setItem(auth.SESSION_CHANGE_KEY,'foreign-notice');assert.equal(scope(),false);assert.equal(auth.isSessionNoticeCurrent(),false);
""")

    def test_logout_retry_retains_original_context_and_superseded_login_supplies_its_own_context(self):
        self.run_case(r"""
await sign();const contexts=[];api.post=async(_,body,isAuth,headers)=>{contexts.push(headers['X-Studio-Session-Context']);throw new Error('offline');};
await auth.logout();await auth.logout();assert.deepEqual(contexts,['context-a','context-a']);
assert.equal(sessionStorage.getItem('studio_logout_context'),'context-a');
await sign('context-b','fixture-b');assert.equal(sessionStorage.getItem('studio_logout_context'),null);
const old=deferred();api.post=(_,body,isAuth,headers)=>body?old.promise:(contexts.push(headers['X-Studio-Session-Context']),Promise.resolve({ok:true}));
auth.clearSession();const signing=auth.login('fixture-c','fixture-only-password'),leaving=auth.logout();
old.resolve({username:'fixture-c',session_context:'context-c'});await signing;await leaving;assert.equal(contexts.at(-1),'context-c');
""")

    def test_resource_restore_waits_for_shared_guard_and_cannot_revive_removed_owner(self):
        self.run_case(r"""
const held=deferred();router.setResumeGuard(()=>held.promise);let resumed=0,disposed=0;
const scope=router.createRenderScope(content),resources=router.bindViewResources(scope.isActive,{dispose:()=>disposed++,suspend:()=>{},resume:()=>resumed++});await flush();
for(const fn of [...listeners.get('pagehide')])fn({persisted:true});for(const fn of [...listeners.get('pageshow')])fn({persisted:true});
assert.equal(resources.isActive(),false);route('#/assets');held.resolve(true);await flush();assert.equal(resumed,0);assert.equal(disposed,1);
""")

    def test_lifecycle_check_failure_keeps_draft_hidden_and_retry_reveals_same_owner(self):
        self.run_case(r"""
await sign();const chrome=new Node('div');chrome.style={};const get=document.getElementById;
document.getElementById=id=>['sidebar','topbar'].includes(id)?chrome:get(id);
const {startSessionLifecycle}=await import('./studio/components/session-lifecycle.js');let changedCount=0;const lifecycle=startSessionLifecycle(()=>changedCount++);
content.textContent='Retained draft';api.get=async()=>{throw new Error('offline');};const waiting=lifecycle.check();await flush();
assert.equal(content.hidden,true);assert.equal(content.textContent,'Retained draft');assert.ok(document.body.textContent.includes(t('auth.verifyFailed')));
api.get=async()=>same();const retry=walk(document.body,n=>n.tagName==='button'&&n.textContent===t('auth.verifyRetry'))[0];await retry.listeners.click();
assert.equal(await waiting,true);assert.equal(content.hidden,false);assert.equal(changedCount,0);
""")

    def test_lifecycle_foreign_notice_cancels_pending_check_without_adopting_identity(self):
        self.run_case(r"""
await sign();const chrome=new Node('div');chrome.style={};const get=document.getElementById;document.getElementById=id=>['sidebar','topbar'].includes(id)?chrome:get(id);
const {startSessionLifecycle}=await import('./studio/components/session-lifecycle.js');let changedCount=0;const lifecycle=startSessionLifecycle(()=>changedCount++);
const held=deferred();api.get=()=>held.promise;const checking=lifecycle.check();
localStorage.setItem(auth.SESSION_CHANGE_KEY,'foreign');for(const fn of [...listeners.get('storage')])fn({key:auth.SESSION_CHANGE_KEY,newValue:'foreign'});
assert.equal(await checking,false);held.resolve(same());await flush();assert.equal(changedCount,1);assert.equal(auth.getSessionContext(),null);
""")

    def test_lifecycle_old_check_does_not_clear_new_login(self):
        self.run_case(r"""
await sign();const chrome=new Node('div');chrome.style={};const get=document.getElementById;document.getElementById=id=>['sidebar','topbar'].includes(id)?chrome:get(id);
const {startSessionLifecycle}=await import('./studio/components/session-lifecycle.js');let changedCount=0;const lifecycle=startSessionLifecycle(()=>changedCount++);
const held=deferred();api.get=()=>held.promise;const checking=lifecycle.check();route('#/login');
assert.equal(await checking,false);assert.equal(content.hidden,false);await sign('context-b','fixture-b');held.resolve(same());await flush();
assert.equal(await checking,false);assert.equal(changedCount,0);assert.equal(auth.getSessionContext(),'context-b');
""")

    def test_new_login_requests_do_not_wait_for_obsolete_verification(self):
        self.run_case(r"""
await sign();wire();const held=deferred();let posts=0;
globalThis.fetch=async path=>path.endsWith('/session')?held.promise:(posts++,json({ok:true}));
const old=auth.getSession({refresh:true}),rejected=assert.rejects(old,error=>error.name==='AbortError');
await sign('context-b','fixture-b');await api.post('/fixture',{});assert.equal(posts,1);
held.resolve(json(same()));await rejected;assert.equal(auth.getSessionContext(),'context-b');
""")

    def test_login_notice_uses_i18n_and_is_consumed_once(self):
        self.run_case(r"""
const {render}=await import('./studio/pages/login.js');location.hash='#/login';
for(const lang of ['zh-CN','en-US']){
 setLanguage(lang);auth.invalidateExternalSession();await render();assert.ok(content.textContent.includes(t('auth.sessionChanged')));
 await render();assert.ok(!content.textContent.includes(t('auth.sessionChanged')));
}
""")
