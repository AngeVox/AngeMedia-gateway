"""升级阻断、旧后端能力和草稿提示的实际JS模块运行测试。"""
import unittest
from test_web_session_context import SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


class WebStudioUpgradeTest(unittest.TestCase):
    def run_case(self,script):
        self.assertTrue(run_studio_module_script(SETUP+'\n{\n'+script+'\n}\nconsole.log(JSON.stringify({ok:true}));',{})['ok'])

    def test_required_or_old_revision_response_blocks_following_requests_in_both_languages(self):
        for lang in ('zh-CN','en-US'):
            for code in ('session_context_required','studio_upgrade_required'):
                self.run_case("setLanguage('"+lang+"');const code='"+code+"';"+r"""
await sign();wire();let calls=0,reason;setUnauthorizedHandler(value=>{reason=value;},auth.captureSessionScope);
globalThis.fetch=async()=>{calls++;return json({detail:{code}},409);};
await assert.rejects(api.get('/fixture'),error=>error.message===t('auth.upgradeRequired'));assert.equal(reason,'studio_upgrade_required');
await assert.rejects(api.post('/fixture',{}));await assert.rejects(api.stream('/fixture',{onEvent(){}}));assert.throws(()=>api.eventUrl('/fixture'));assert.equal(calls,1);
""")

    def test_late_upgrade_response_does_not_poison_new_session(self):
        self.run_case(r"""
await sign();wire();const held=deferred();globalThis.fetch=()=>held.promise;
const pending=api.get('/fixture'),rejected=assert.rejects(pending);await flush();await sign('context-b','fixture-b');
held.resolve(json({detail:{code:'studio_upgrade_required'}},409));await rejected;
globalThis.fetch=async()=>json({ok:true});await api.get('/fixture');assert.equal(auth.getSessionContext(),'context-b');
""")

    def test_old_backend_login_response_is_not_accepted_as_current_web_session(self):
        self.run_case(r"""
wire();let reason;setUnauthorizedHandler(value=>{reason=value;},auth.captureSessionScope);
api.post=async()=>({username:'fixture-a'});await assert.rejects(auth.login('fixture-a','fixture-password'));
assert.equal(reason,'studio_upgrade_required');assert.equal(auth.getSessionContext(),null);
""")

    def test_logged_out_bootstrap_does_not_require_context(self):
        self.run_case(r"""
wire();api.get=async()=>({authenticated:false});assert.equal(await auth.getSession(),null);assert.equal(expired,0);
""")

    def test_versioned_api_module_sends_revision_for_http_logout_and_eventsource(self):
        self.run_case(r"""
const fs=await import('node:fs/promises');const revision='r-123456789012345678901234';
await fs.mkdir(new URL('./assets/'+revision+'/',import.meta.url),{recursive:true});
await fs.cp(new URL('./studio/',import.meta.url),new URL('./assets/'+revision+'/studio',import.meta.url),{recursive:true});
const {api:versioned,setSessionContextProvider:setContext}=await import('./assets/'+revision+'/studio/api.js');setContext(()=> 'fixture-context');
const calls=[];globalThis.fetch=async(_,options)=>{calls.push(options);return json({ok:true});};
await versioned.get('/fixture');await versioned.post('/admin/logout',undefined,true,{'X-Studio-Session-Context':'fixture-context'});
assert.ok(calls.every(row=>row.headers['X-Studio-Revision']===revision));assert.ok(versioned.eventUrl('/fixture').includes('studio_revision='+revision));
""")

    def test_upgrade_preserves_original_draft_without_automatic_reload_and_masks_other_identity(self):
        for lang in ('zh-CN','en-US'):
            self.run_case("setLanguage('"+lang+"');"+r"""
await sign();const chrome=new Node('div');chrome.style={};const get=document.getElementById;
document.getElementById=id=>['sidebar','topbar'].includes(id)?chrome:get(id);
const prompt=new Node('textarea');prompt.value='Original draft';content.append(prompt);
const query=content.querySelector.bind(content);content.querySelector=selector=>selector==='textarea[name="prompt"]'?prompt:query(selector);
let reloads=0;location.reload=()=>reloads++;
const {startSessionLifecycle}=await import('./studio/components/session-lifecycle.js');const lifecycle=startSessionLifecycle(()=>{});
lifecycle.upgrade();assert.equal(content.hidden,true);assert.equal(prompt.value,'Original draft');assert.equal(reloads,0);
assert.ok(document.body.textContent.includes(t('auth.upgradeRequired')));assert.equal(await lifecycle.check(),false);
const feedback=walk(document.body,n=>hasClass(n,'session-verification'))[0];const saved=walk(feedback,n=>n.tagName==='textarea')[0];assert.equal(saved.value,'Original draft');
for(const fn of [...listeners.get('storage')])fn({key:auth.SESSION_CHANGE_KEY});assert.equal(saved.value,'');
const reload=walk(feedback,n=>n.tagName==='button')[0];reload.listeners.click();assert.equal(reloads,1);
""")
