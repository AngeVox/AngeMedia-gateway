"""路由、读取和会话缓存的代次回归；浏览器另验真实挂载与交互。"""
import unittest

from test_web_modal_runtime import SETUP as MODAL_SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


SETUP = MODAL_SETUP + r"""
globalThis.location={hash:'#/jobs'};
const storage=new Map();
globalThis.localStorage=globalThis.sessionStorage={getItem:key=>storage.get(key)||null,
 setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)};
const content=new Node('main'),toasts=new Node('div');document.body.append(content,toasts);
document.getElementById=id=>id==='toast-container'?toasts:content;
document.querySelectorAll=()=>[];
Node.prototype.querySelectorAll=function(selector){
 return walk(this,n=>selector.startsWith('.')?hasClass(n,selector.slice(1)):selector.split(',').includes(n.tagName));
};
globalThis.setTimeout=()=>0;
const {api}=await import('./studio/api.js');
const router=await import('./studio/router.js');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const route=hash=>{location.hash=hash;[...(listeners.get('hashchange')||[])].forEach(fn=>fn());};
const fixtureJob=(id,prompt)=>({id,job_id:id,prompt,kind:'image',status:'succeeded',created_at:'2026-11-03T00:00:00Z'});
const list=data=>({data,total:data.length});
"""


class RenderLifecycleRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(
            SETUP + script + "\nconsole.log(JSON.stringify({ok:true}));", {}
        )["ok"])

    def test_route_generation_invalidates_a_b_a_and_same_hash_dispatch(self):
        self.run_case(r"""
const visits=[];router.register('#/jobs',(_,scope)=>visits.push(scope));router.register('#/assets',()=>{});router.start();
const old=visits[0];route('#/assets');route('#/jobs');assert.equal(old.isActive(),false);
const current=visits.at(-1);assert.equal(current.isActive(),true);route('#/jobs');assert.equal(current.isActive(),false);
assert.equal(visits.at(-1).isActive(),true);
""")

    def test_container_owner_and_latest_request_are_independent_boundaries(self):
        self.run_case(r"""
const first=router.createRenderScope(content),old=first.nextRequest(),latest=first.nextRequest();
assert.equal(old(),false);assert.equal(latest(),true);
const second=router.createRenderScope(content);assert.equal(first.isActive(),false);assert.equal(latest(),false);
assert.equal(second.nextRequest()(),true);content.remove();assert.equal(second.isActive(),false);
""")

    def test_creator_bootstrap_drops_late_success_and_failure(self):
        self.run_case(r"""
for(const media of ['image','video'])for(const failure of [false,true]){
 location.hash='#/generate/'+media;const pending=deferred();let calls=0;
 api.get=()=>{calls++;return pending.promise;};
 const {render}=await import('./studio/features/generate-'+media+'/page.js');const rendering=render();
 location.hash='#/jobs';content.textContent='Current jobs page';
 if(failure)pending.reject(new Error('late error'));else pending.resolve({});await rendering;
 assert.equal(content.textContent,'Current jobs page');assert.equal(calls,media==='image'?1:2);
}
""")

    def test_direct_detail_pages_drop_late_success_and_failure(self):
        self.run_case(r"""
for(const page of ['jobs-detail','assets-detail'])for(const failure of [false,true]){
 location.hash='#/'+page+'/fixture';const pending=deferred();api.get=()=>pending.promise;
 const {render}=await import('./studio/pages/'+page+'.js');const rendering=render({id:'fixture'});
 location.hash='#/assets';content.textContent='Current assets page';
 if(failure)pending.reject(new Error('late error'));else pending.resolve({data:{}});await rendering;
 assert.equal(content.textContent,'Current assets page');
}
""")

    def test_management_bootstrap_drops_late_success_and_failure(self):
        self.run_case(r"""
for(const page of ['dashboard','providers','system','gateway-keys','diagnostics'])for(const failure of [false,true]){
 location.hash='#/'+page;const pending=deferred();api.get=()=>pending.promise;
 const {render}=await import('./studio/features/'+page+'/page.js');const rendering=render();
 location.hash='#/jobs';content.textContent='Current jobs page';
 if(failure)pending.reject(new Error('old read'));else pending.resolve({});await rendering;
 assert.equal(content.textContent,'Current jobs page');
}
""")

    def test_jobs_same_page_reload_commits_latest_list_before_using_its_detail(self):
        self.run_case(r"""
const {render}=await import('./studio/features/jobs/page.js');
api.get=async()=>list([fixtureJob('initial','Initial row')]);await render();
const reload=controls(content).find(n=>n.textContent==='Refresh').listeners.click;
const first=deferred(),second=deferred();let calls=0;
api.get=()=>++calls===1?first.promise:second.promise;
const old=reload(),current=reload();second.resolve(list([fixtureJob('new-job','Newest row')]));await current;
first.resolve(list([fixtureJob('old-job','Obsolete row')]));await old;
assert.ok(content.textContent.includes('Newest row'));assert.ok(!content.textContent.includes('Obsolete row'));
let detailPath;api.get=async path=>{detailPath=path;return {data:fixtureJob('new-job','Newest row')};};
await controls(content).find(n=>n.textContent==='Details').listeners.click();
assert.equal(detailPath,'/admin/jobs/new-job');
""")

    def test_jobs_stale_failure_does_not_replace_latest_success(self):
        self.run_case(r"""
const {render}=await import('./studio/features/jobs/page.js');api.get=async()=>list([]);await render();
const reload=controls(content).find(n=>n.textContent==='Refresh').listeners.click;
const first=deferred();api.get=()=>first.promise;const old=reload();
api.get=async()=>list([fixtureJob('new-job','Newest row')]);await reload();
first.reject(new Error('old failure'));await old;assert.ok(content.textContent.includes('Newest row'));
""")

    def test_jobs_route_close_drops_detail_and_new_visit_clears_old_selection(self):
        self.run_case(r"""
const {render}=await import('./studio/features/jobs/page.js');api.get=async()=>list([fixtureJob('old-job','Old row')]);await render();
const pending=deferred();api.get=()=>pending.promise;
const detail=controls(content).find(n=>n.textContent==='Details').listeners.click();
location.hash='#/assets';content.textContent='Current assets page';pending.resolve({data:fixtureJob('old-job','Late detail')});await detail;
assert.equal(content.textContent,'Current assets page');assert.equal(toasts.children.length,0);
location.hash='#/jobs';api.get=async()=>list([]);await render();
assert.equal(content.querySelector('.job-detail-drawer-layer').hidden,true);
""")


class SessionReadRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(
            SETUP + "\nconst auth=await import('./studio/auth.js');\n" + script +
            "\nconsole.log(JSON.stringify({ok:true}));", {}
        )["ok"])

    def test_older_negative_or_failed_check_cannot_clear_newer_session(self):
        self.run_case(r"""
for(const failure of [false,true]){
 auth.clearSession();const first=deferred(),second=deferred();let calls=0;
 api.get=()=>++calls===1?first.promise:second.promise;
 const old=auth.getSession(),current=auth.getSession();second.resolve({authenticated:true,username:'current'});await current;
 if(failure)first.reject(new Error('old error'));else first.resolve({authenticated:false});await old;
 assert.equal((await auth.getSession()).username,'current');assert.equal(calls,2);
}
""")

    def test_older_positive_check_cannot_revive_newer_negative_check(self):
        self.run_case(r"""
const first=deferred(),second=deferred();let calls=0;api.get=()=>++calls===1?first.promise:second.promise;
const old=auth.getSession(),current=auth.getSession();second.resolve({authenticated:false});assert.equal(await current,null);
first.resolve({authenticated:true,username:'obsolete'});assert.equal(await old,null);
api.get=async()=>({authenticated:false});assert.equal(await auth.getSession(),null);
""")

    def test_clear_session_invalidates_pending_read(self):
        self.run_case(r"""
const pending=deferred();api.get=()=>pending.promise;const reading=auth.getSession();auth.clearSession();
pending.resolve({authenticated:true,username:'obsolete'});assert.equal(await reading,null);
api.get=async()=>({authenticated:false});assert.equal(await auth.getSession(),null);
""")

    def test_login_invalidates_reads_started_before_and_during_it(self):
        self.run_case(r"""
const before=deferred(),during=deferred(),signing=deferred();let calls=0;
api.get=()=>++calls===1?before.promise:during.promise;api.post=()=>signing.promise;
const old=auth.getSession(),login=auth.login('fixture','fixture-password'),middle=auth.getSession();
signing.resolve({username:'current'});await login;
before.resolve({authenticated:false});during.resolve({authenticated:false});await old;await middle;
assert.equal((await auth.getSession()).username,'current');
""")

    def test_logout_completion_cannot_overwrite_newer_local_login(self):
        self.run_case(r"""
const pending=deferred();api.post=()=>pending.promise;const logout=auth.logout();
api.post=async()=>({username:'current'});const login=auth.login('fixture','fixture-password');
pending.resolve({});await logout;await login;assert.equal(location.hash,'#/jobs');assert.equal((await auth.getSession()).username,'current');
""")
