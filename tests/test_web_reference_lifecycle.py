"""参考素材所有权、缓存恢复与迟到上传的实际模块运行回归。"""
import unittest

from test_web_submission_snapshot import SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


LIFECYCLE = SETUP + r"""
const created=[],revoked=[];
URL.createObjectURL=()=>{const url='blob:fixture-'+created.length;created.push(url);return url;};
URL.revokeObjectURL=url=>revoked.push(url);
const observers=new Set();
globalThis.MutationObserver=class {
 constructor(callback){this.callback=callback;}observe(){observers.add(this);}disconnect(){observers.delete(this);}
};
const mutations=()=>[...observers].forEach(observer=>observer.callback());
const transition=(name,persisted)=>[...(listeners.get(name)||[])].forEach(fn=>fn({persisted}));
const {createReferenceUpload}=await import('./studio/features/generate-image/reference-upload.js');
function uploader(options={}){const target=new Node('div');content.append(target);
 const upload=createReferenceUpload({target,...options});return {target,upload,input:files(target)[0]};}
"""


class ReferenceLifecycleRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(LIFECYCLE + script + '\nconsole.log(JSON.stringify({ok:true}));', {})['ok'])

    def test_dispose_releases_files_paths_and_listeners_without_resurrection(self):
        self.run_case(r"""
const {upload,input,target}=uploader();choose(input,[file('one.png')]);const stale=input.listeners.change;
api.get=async()=>({limits:{max_files:1}});api.upload=async()=>({data:[{url_path:'/uploads/one.png'}]});await upload.prepare();
upload.dispose();upload.dispose();upload.resume();input.files=[file('late.png')];stale();
assert.deepEqual(created,['blob:fixture-0']);assert.deepEqual(revoked,created);assert.equal(upload.hasPendingFile(),false);
assert.equal(upload.value(),null);assert.equal(input.value,'');assert.equal(input.disabled,true);assert.equal(input.listeners.change,undefined);
assert.equal(walk(target,n=>n.tagName==='img')[0].src,'');await assert.rejects(upload.prepare());
""")

    def test_suspend_preserves_draft_and_confirmed_prefix_with_no_automatic_upload(self):
        self.run_case(r"""
const {upload,input}=uploader({multiple:true,maxFiles:2});choose(input,[file('a.png'),file('b.png')]);
api.get=async()=>({limits:{max_files:1}});const names=[];let fail=true;api.upload=async(_,body)=>{
 const name=body.get('files').name;names.push(name);if(name==='b.png'&&fail){fail=false;throw new Error('fixture');}
 return {data:[{url_path:'/uploads/'+name}]};};
await assert.rejects(upload.prepare());const old=upload.capture();upload.suspend();upload.suspend();
assert.equal(upload.hasPendingFile(),true);assert.deepEqual(upload.value(),['/uploads/a.png']);assert.deepEqual(revoked,['blob:fixture-0']);
await assert.rejects(upload.prepare());upload.resume();upload.resume();assert.equal(created.length,2);assert.equal(names.length,2);
await assert.rejects(old());assert.deepEqual(await upload.prepare(),['/uploads/a.png','/uploads/b.png']);
assert.deepEqual(names,['a.png','b.png','b.png']);upload.dispose();assert.deepEqual(revoked,created);
""")

    def test_old_limits_or_upload_completion_cannot_revive_released_preparation(self):
        self.run_case(r"""
for(const phase of ['limits','upload'])for(const boundary of ['dispose','suspend']){
 const {upload,input}=uploader();choose(input,[file('old.png')]);const held=deferred();let calls=0;
 api.get=()=>phase==='limits'?held.promise:Promise.resolve({limits:{max_files:1}});
 api.upload=()=>{calls++;return held.promise;};const pending=upload.prepare(),rejected=assert.rejects(pending);await flush();
 upload[boundary]();if(boundary==='suspend')upload.resume();
 held.resolve(phase==='limits'?{limits:{max_files:1}}:{data:[{url_path:'/uploads/old.png'}]});await rejected;
 assert.equal(calls,phase==='limits'?0:1);assert.equal(upload.value(),null);upload.dispose();
}
assert.deepEqual(revoked.sort(),created.sort());
""")

    def test_new_preparation_is_not_cleared_or_overwritten_by_old_completion(self):
        self.run_case(r"""
const {upload,input,target}=uploader();choose(input,[file('one.png')]);api.get=async()=>({limits:{max_files:1}});
const old=deferred(),fresh=deferred();let calls=0;api.upload=()=>++calls===1?old.promise:fresh.promise;
const first=upload.prepare(),rejected=assert.rejects(first);await flush();upload.suspend();upload.resume();
const second=upload.prepare();await flush();old.resolve({data:[{url_path:'/uploads/stale.png'}]});await rejected;
assert.equal(walk(target,n=>n.className==='ref-upload-status field-help')[0].textContent,t('generateImage.uploading'));
fresh.resolve({data:[{url_path:'/uploads/fresh.png'}]});assert.equal(await second,'/uploads/fresh.png');assert.equal(calls,2);upload.dispose();
""")

    def test_view_owner_disposes_route_rebuild_and_removed_root_once(self):
        self.run_case(r"""
for(const boundary of ['route','rebuild','remove','pagehide']){
 location.hash='#/jobs';let disposed=0;const scope=router.createRenderScope(content);
 const resources=router.bindViewResources(scope.isActive,{dispose:()=>disposed++,suspend:()=>{},resume:()=>{}});await flush();
 assert.equal(resources.isActive(),true);
 if(boundary==='route')route('#/assets');else if(boundary==='rebuild'){router.createRenderScope(content);mutations();}
 else if(boundary==='remove'){content.remove();mutations();document.body.append(content);}else transition('pagehide',false);
 assert.equal(disposed,1);resources.close();mutations();assert.equal(disposed,1);assert.equal(observers.size,0);
 for(const event of ['hashchange','pagehide','pageshow'])assert.equal(listeners.get(event)?.size||0,0);
}
""")

    def test_persisted_owner_resumes_once_and_cleanup_survives_throwing_hook(self):
        self.run_case(r"""
let suspended=0,resumed=0,disposed=0;const resources=router.bindViewResources(()=>true,
 {dispose:()=>{disposed++;throw new Error('fixture');},suspend:()=>suspended++,resume:()=>resumed++});await flush();
transition('pagehide',true);transition('pagehide',true);assert.equal(resources.isActive(),false);
transition('pageshow',true);transition('pageshow',true);assert.equal(resources.isActive(),true);
assert.equal(suspended,1);assert.equal(resumed,1);assert.throws(resources.close);resources.close();assert.equal(disposed,1);assert.equal(observers.size,0);
for(const event of ['hashchange','pagehide','pageshow'])assert.equal(listeners.get(event).size,0);
""")

    def test_creator_owner_releases_previews_on_route_and_same_hash_replacement(self):
        self.run_case(r"""
for(const media of ['image','video'])for(const boundary of ['route','rebuild']){
 await creator(media);const input=files(content)[0];choose(input,[file('preview.png')]);const before=revoked.length;
 if(boundary==='route')route('#/jobs');else {router.createRenderScope(content);mutations();}
 assert.equal(revoked.length,before+1);assert.equal(input.value,'');assert.equal(input.disabled,true);
 mutations();assert.equal(observers.size,0);
}
""")

    def test_creator_persisted_return_keeps_draft_but_old_submit_stays_invalid(self):
        self.run_case(r"""
for(const media of ['image','video'])for(const settleBeforeReturn of [false,true]){
 posts.length=0;const submit=await creator(media),input=files(content)[0];choose(input,[file('original.png')]);
 const held=deferred();api.upload=()=>held.promise;const pending=submit();await flush();transition('pagehide',true);
 assert.equal(input.value||'', '');assert.equal(revoked.at(-1),created.at(-1));
 if(settleBeforeReturn){held.resolve({data:[{url_path:'/uploads/old.png'}]});await pending;}
 transition('pageshow',true);assert.equal(named('prompt').value,'Original prompt');assert.equal(input.disabled||false,false);
 if(!settleBeforeReturn){held.resolve({data:[{url_path:'/uploads/old.png'}]});await pending;}
 assert.equal(posts.length,0);const button=submitControl(media);
 assert.equal(button.disabled,false);api.upload=async()=>({data:[{url_path:'/uploads/retry.png'}]});await submit();assert.equal(posts.length,1);
 route('#/jobs');assert.equal(observers.size,0);
}
""")

    def test_job_ack_after_persisted_return_does_not_restart_old_result_tracking(self):
        self.run_case(r"""
for(const media of ['image','video']){
 const submit=await creator(media),held=deferred();let posted=0,reads=0;
 api.post=()=>{posted++;return held.promise;};const get=api.get;api.get=path=>{if(path.includes('accepted-old'))reads++;return get(path);};
 const pending=submit();await flush();assert.equal(posted,1);transition('pagehide',true);transition('pageshow',true);
 held.resolve({job_id:'accepted-old',status:'queued'});await pending;await flush();
 assert.equal(reads,0);assert.equal(content.textContent.includes('accepted-old'),false);
 const button=submitControl(media);
 assert.equal(button.disabled,false);route('#/jobs');assert.equal(observers.size,0);
}
""")
