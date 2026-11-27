"""上传准备、提交快照和页面/会话归属的实际模块运行测试。"""
import unittest

from test_web_render_lifecycle import SETUP as VIEW_SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


SETUP = VIEW_SETUP + r"""
window.location={origin:'http://testserver'};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const file=name=>new File(['fixture'],name,{type:'image/png'});
const choose=(input,files)=>{input.files=files;input.listeners.change();};
const named=name=>walk(content,node=>node.name===name)[0];
const files=target=>walk(target,node=>node.type==='file');
const auth=await import('./studio/auth.js');
const {t}=await import('./studio/i18n.js');
function submitControl(media){
 const key=media==='image'?'generateImage.operationEdit':'generateVideo.submit';
 return controls(content).find(node=>node.textContent===t(key));
}
const imageModel={id:'fixture-image',provider_id:'fixture',media_type:'image',selectable:true,status:'release',size_presets:['1024x1024'],
 operations:{image_edit:{supported:true,params:{size:{kind:'size'},seed:{kind:'seed'}},refs:[
 {roles:['image'],provider_field:'image',max_count:1,formats:['url']},{roles:['mask'],provider_field:'mask',max_count:1}]}}};
const videoModel={id:'fixture-video',provider_id:'fixture',media_type:'video',selectable:true,status:'release',
 capabilities:{image_to_video:true},ref_input_spec:{formats:['url'],roles:['first_frame','last_frame','images'],max_total:3},
 params:{mode:'string',seconds:'integer'},param_specs:{mode:{kind:'enum',enum_values:['text','keyframe','reference'],default:'reference'}},
 video_contract:{max_audios:2,max_videos:1,max_total:6}};
const provider={id:'fixture',ui_group:'builtin_media',runtime:{enabled:true},requires_key:false};
const posts=[];api.post=async(path,payload)=>{posts.push({path,payload:JSON.parse(JSON.stringify(payload))});throw new Error('fixture ACK unavailable');};
async function creator(media){
 location.hash='#/generate/'+media;router.start();
 api.get=async path=>path==='/admin/catalog'?{providers:[provider],models:[imageModel,videoModel]}:
  path.startsWith('/uploads')?{limits:{max_files:1},data:[]}:{data:[]};
 const {render}=await import('./studio/features/generate-'+media+'/page.js');await render();
 named('provider').value=media==='image'?'catalog:fixture':'fixture';named('provider').listeners.change();
 named('prompt').value='Original prompt';if(media==='image')named('size').value='1024x1024';
 return submitControl(media).listeners.click;
}
"""


class SubmissionSnapshotRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(SETUP + script + '\nconsole.log(JSON.stringify({ok:true}));', {})['ok'])

    def test_mask_replacement_during_image_upload_never_uploads_new_mask(self):
        self.run_case(r"""
const {createOperationControls}=await import('./studio/features/generate-image/operation-controls.js');
for(const clear of [false,true]){
 const target=new Node('div');content.append(target);const operation=createOperationControls({target});operation.sync(imageModel);
 const inputs=files(target);choose(inputs[0],[file('original.png')]);choose(inputs[1],[file('original-mask.png')]);
 const held=deferred();let uploads=0;api.get=async()=>({limits:{max_files:1}});api.upload=()=>{uploads++;return held.promise;};
 const preparing=operation.prepare();const rejected=assert.rejects(preparing,error=>error.safe?.code==='reference_selection_changed');await flush();
 choose(inputs[1],clear?[]:[file('changed-mask.png')]);held.resolve({data:[{url_path:'/uploads/original.png'}]});await rejected;
 assert.equal(uploads,1);target.remove();
}
""")

    def test_shared_upload_capture_does_not_add_new_file_to_empty_snapshot(self):
        self.run_case(r"""
const {createReferenceUpload}=await import('./studio/features/generate-image/reference-upload.js');const target=new Node('div');content.append(target);
const upload=createReferenceUpload({target}),captured=upload.capture();choose(files(target)[0],[file('new.png')]);let calls=0;api.upload=()=>{calls++;};
assert.equal(await captured(),null);assert.equal(calls,0);assert.equal(upload.hasPendingFile(),true);
""")

    def test_partial_upload_retry_reuses_confirmed_batches_only(self):
        self.run_case(r"""
const {createReferenceUpload}=await import('./studio/features/generate-image/reference-upload.js');const target=new Node('div');content.append(target);
const upload=createReferenceUpload({target,multiple:true,maxFiles:3});choose(files(target)[0],[file('a.png'),file('b.png'),file('c.png')]);
api.get=async()=>({limits:{max_files:1}});const names=[];let fail=true;api.upload=async(path,body)=>{
 const name=body.get('files').name;names.push(name);if(name==='b.png'&&fail){fail=false;throw new Error('fixture failure');}return {data:[{url_path:'/uploads/'+name}]};};
await assert.rejects(upload.prepare());assert.deepEqual(upload.value(),['/uploads/a.png']);
assert.deepEqual(await upload.prepare(),['/uploads/a.png','/uploads/b.png','/uploads/c.png']);assert.deepEqual(names,['a.png','b.png','b.png','c.png']);
""")

    def test_inactive_scope_stops_before_upload_and_between_batches(self):
        self.run_case(r"""
const {createReferenceUpload}=await import('./studio/features/generate-image/reference-upload.js');
for(const beforeUpload of [true,false]){
 const target=new Node('div');content.append(target);const upload=createReferenceUpload({target,multiple:true,maxFiles:2});choose(files(target)[0],[file('a.png'),file('b.png')]);
 const held=deferred();let active=true,calls=0;api.get=()=>beforeUpload?held.promise:Promise.resolve({limits:{max_files:1}});api.upload=()=>{calls++;return held.promise;};
 const preparing=upload.capture(()=>active)();const rejected=assert.rejects(preparing);await flush();active=false;
 held.resolve(beforeUpload?{limits:{max_files:1}}:{data:[{url_path:'/uploads/a.png'}]});await rejected;assert.equal(calls,beforeUpload?0:1);target.remove();
}
""")

    def test_image_payload_keeps_prompt_size_and_zero_seed_before_upload(self):
        self.run_case(r"""
const submit=await creator('image');choose(files(content)[0],[file('original.png')]);named('operation_seed').value='0';
const held=deferred();api.upload=()=>held.promise;const submitting=submit();await flush();
named('prompt').value='Changed prompt';named('size').value='custom';named('custom_size').value='2048x2048';named('operation_seed').value='42';
held.resolve({data:[{url_path:'/uploads/original.png'}]});await submitting;
assert.equal(posts.length,1);assert.equal(posts[0].payload.prompt,'Original prompt');assert.equal(posts[0].payload.size,'1024x1024');assert.equal(posts[0].payload.seed,0);
assert.equal(posts[0].payload.image,'/uploads/original.png');assert.equal(named('prompt').value,'Changed prompt');
assert.equal(listeners.get('pagehide').size,1);route('#/jobs');assert.equal(listeners.get('pagehide').size,0);
""")

    def test_video_payload_uses_original_links_audio_video_and_mode(self):
        self.run_case(r"""
const submit=await creator('video');choose(files(content)[0],[file('original.png')]);named('reference_urls').value='https://example.com/original.png';
named('audios').value='https://example.com/original.mp3';named('video_url').value='https://example.com/original.mp4';named('video_start_seconds').value='0';named('video_require_audio').checked=false;
const held=deferred();api.upload=()=>held.promise;const submitting=submit();await flush();
named('prompt').value='Changed prompt';named('reference_urls').value='https://example.com/changed.png';named('first_frame').value='https://example.com/new-frame.png';
named('audios').value='https://example.com/changed.mp3';named('video_url').value='https://example.com/changed.mp4';named('video_start_seconds').value='9';named('video_require_audio').checked=true;
held.resolve({data:[{url_path:'/uploads/original.png'}]});await submitting;
assert.equal(posts.length,1);const payload=posts[0].payload;assert.equal(payload.prompt,'Original prompt');assert.equal(payload.mode,'reference');
assert.deepEqual(payload.images,['/uploads/original.png','https://example.com/original.png']);assert.deepEqual(payload.audios,['https://example.com/original.mp3']);
assert.deepEqual(payload.videos,[{url:'https://example.com/original.mp4',start_seconds:0,require_audio:false}]);assert.equal(payload.first_frame,undefined);
""")

    def test_route_a_b_a_pagehide_and_session_change_prevent_job_post(self):
        self.run_case(r"""
for(const media of ['image','video'])for(const boundary of ['route','pagehide','session']){
 posts.length=0;const submit=await creator(media);choose(files(content)[0],[file('original.png')]);const held=deferred();api.upload=()=>held.promise;
 const submitting=submit();await flush();if(boundary==='route'){route('#/jobs');route('#/generate/'+media);}
 else if(boundary==='pagehide')[...(listeners.get('pagehide')||[])].forEach(fn=>fn());else auth.clearSession();
 held.resolve({data:[{url_path:'/uploads/original.png'}]});await submitting;assert.equal(posts.length,0);
 assert.equal(listeners.get('pagehide').size,boundary==='session'?1:0);route('#/jobs');assert.equal(listeners.get('pagehide').size,0);
}
""")

    def test_changed_file_cancels_old_job_and_explicit_retry_uses_new_file(self):
        self.run_case(r"""
for(const media of ['image','video']){
 posts.length=0;const submit=await creator(media),input=files(content)[0];choose(input,[file('old.png')]);const held=deferred();api.upload=()=>held.promise;
 const submitting=submit();await flush();choose(input,[file('new.png')]);held.resolve({data:[{url_path:'/uploads/old.png'}]});await submitting;
 assert.equal(posts.length,0);assert.ok(content.textContent.includes('reference files changed'));
 api.upload=async()=>({data:[{url_path:'/uploads/new.png'}]});await submit();assert.equal(posts.length,1);
 const refs=media==='image'?posts[0].payload.image:posts[0].payload.images[0];assert.equal(refs,'/uploads/new.png');
}
""")

    def test_video_keyframe_conflicts_and_image_limits_remain_enforced(self):
        self.run_case(r"""
const {applyVideoReferences}=await import('./studio/features/generate-video/reference-payload.js');
const values={asset:'',assets:[],firstFrame:'',lastFrame:'https://example.com/last.png',urls:''};const payload={mode:'keyframe'};
applyVideoReferences(payload,videoModel,values,['/uploads/first.png']);assert.equal(payload.first_frame,'/uploads/first.png');assert.equal(payload.last_frame,values.lastFrame);
assert.throws(()=>applyVideoReferences({mode:'keyframe'},videoModel,{...values,firstFrame:'https://example.com/duplicate.png'},['/uploads/first.png']));
assert.throws(()=>applyVideoReferences({mode:'keyframe'},videoModel,{...values,urls:'https://example.com/reference.png'},[]));
assert.throws(()=>applyVideoReferences({mode:'reference'},videoModel,{...values,lastFrame:'',assets:['a','b','c'],urls:'d'},[]));
const local={capabilities:{image_to_video:true}};const single={};applyVideoReferences(single,local,{asset:'/uploads/original.png'},null);assert.equal(single.image,'/uploads/original.png');
""")

    def test_pending_scope_close_is_idempotent_and_does_not_change_route(self):
        self.run_case(r"""
const {createPendingScope}=await import('./studio/router.js');let current=true;const scope=createPendingScope(()=>current);
assert.equal(scope.isActive(),true);current=false;assert.equal(scope.isActive(),false);current=true;
scope.close();scope.close();assert.equal(scope.isActive(),false);assert.equal(listeners.get('pagehide').size,0);assert.equal(location.hash,'#/jobs');
""")
