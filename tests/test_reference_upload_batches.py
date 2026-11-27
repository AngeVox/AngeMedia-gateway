"""运行原浏览器模块，验证型号选择容量与后台上传批次容量各自生效。"""
import unittest

from test_web_studio_generate_image_operation_controls import run_studio_module_script
from angemedia_gateway.providers.catalog.api import catalog_api_response
from angemedia_gateway.providers.catalog.loader import load_provider_catalog


PREAMBLE = """
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { File } from 'node:buffer';
class FakeElement {
  constructor(tagName) {
    this.tagName=tagName.toUpperCase();this.children=[];this.dataset={};this.className='';
    this.hidden=false;this.value='';this.files=[];this.listeners={};this.multiple=false;
  }
  appendChild(child) { this.children.push(child);return child; }
  addEventListener(name,fn) { this.listeners[name]=fn; }
  setAttribute(key,value) { this[key]=String(value); }
  set textContent(value) { this._textContent=String(value);if(value==='')this.children=[]; }
  get textContent() { return this._textContent || ''; }
}
globalThis.document={createElement:(name)=>new FakeElement(name),createTextNode:(text)=>({children:[],textContent:text})};
globalThis.window={location:{origin:'https://gateway.example'}};
globalThis.URL.createObjectURL=()=> 'blob:fixture';globalThis.URL.revokeObjectURL=()=>{};
const { createReferenceUpload }=await import('./studio/features/generate-image/reference-upload.js');
const { api }=await import('./studio/api.js');
const turn=()=>new Promise((resolve)=>setImmediate(resolve));
function walk(node,predicate) {
  if(predicate(node))return node;
  for(const child of node.children || []) {const found=walk(child,predicate);if(found)return found;}
  return null;
}
const files=(count,prefix='frame')=>Array.from({length:count},(_,i)=>new File(['fixture'],prefix+i+'.png',{type:'image/png'}));
const response=(batch)=>({data:batch.map((file)=>({url_path:'/uploads/'+file.name}))});
function widget(maxFiles=16) {
  const target=new FakeElement('div');const upload=createReferenceUpload({target,multiple:true,maxFiles});
  const input=walk(target,(node)=>node.type==='file');
  const choose=(values)=>{input.files=values;input.listeners.change();};
  return {upload,choose,target};
}
"""


class ReferenceUploadBatchTest(unittest.TestCase):
    def run_script(self,body,payload=None):
        result=run_studio_module_script(PREAMBLE+body+"\nconsole.log(JSON.stringify({ok:true}));",payload or {})
        self.assertTrue(result['ok'])

    def test_sixteen_files_use_backend_sized_batches_in_order_and_cache_success(self):
        self.run_script("""
        const {upload,choose}=widget();const selected=files(16);const batches=[];let reads=0;
        api.get=async(path)=>{assert.equal(path,'/uploads?limit=1');reads++;return {limits:{max_files:10}};};
        api.upload=async(path,form)=>{assert.equal(path,'/uploads');const batch=form.getAll('files');
          batches.push(batch.map((file)=>file.name));assert.equal(form.get('roles'),batch.map(()=> 'reference').join(','));return response(batch);};
        choose(selected);assert.equal(upload.hasPendingFile(),true);
        const prepared=await upload.prepare();
        assert.deepEqual(batches.map((batch)=>batch.length),[10,6]);
        assert.deepEqual(prepared,selected.map((file)=>'/uploads/'+file.name));
        assert.deepEqual(await upload.prepare(),prepared);assert.equal(batches.length,2);assert.equal(reads,1);
        """)

    def test_retry_preserves_confirmed_batches_and_only_retries_unfinished_files(self):
        self.run_script("""
        const {upload,choose}=widget();const selected=files(5);const batches=[];
        api.get=async()=>({limits:{max_files:2}});
        api.upload=async(path,form)=>{const batch=form.getAll('files');batches.push(batch.map((file)=>file.name));
          if(batches.length===2)throw new Error('fixture failed batch');return response(batch);};
        choose(selected);await assert.rejects(upload.prepare());
        assert.equal(upload.hasPendingFile(),true);
        assert.deepEqual(await upload.prepare(),selected.map((file)=>'/uploads/'+file.name));
        assert.deepEqual(batches,[['frame0.png','frame1.png'],['frame2.png','frame3.png'],['frame2.png','frame3.png'],['frame4.png']]);
        """)

    def test_concurrent_prepare_calls_share_one_upload_progress(self):
        self.run_script("""
        const {upload,choose}=widget();const selected=files(16);let reads=0;const batches=[];
        api.get=async()=>{reads++;return {limits:{max_files:10}};};
        api.upload=async(path,form)=>{const batch=form.getAll('files');batches.push(batch.length);await turn();return response(batch);};
        choose(selected);const [first,second]=await Promise.all([upload.prepare(),upload.prepare()]);
        assert.deepEqual(first,second);assert.equal(first.length,16);assert.equal(reads,1);assert.deepEqual(batches,[10,6]);
        """)

    def test_old_upload_response_cannot_bind_to_new_selection(self):
        self.run_script("""
        const {upload,choose}=widget();let release;let posts=0;
        const gate=new Promise((resolve)=>{release=resolve;});api.get=async()=>({limits:{max_files:10}});
        api.upload=async(path,form)=>{const batch=form.getAll('files');posts++;if(posts===1)await gate;return response(batch);};
        choose(files(2,'old'));const old=upload.prepare();const rejected=assert.rejects(old);await turn();
        choose(files(1,'new'));release();await rejected;assert.deepEqual(upload.value(),[]);
        assert.deepEqual(await upload.prepare(),['/uploads/new0.png']);assert.equal(posts,2);
        """)

    def test_stale_limit_response_cannot_return_a_new_selection_as_old_success(self):
        self.run_script("""
        const {upload,choose}=widget();let release;let reads=0;let posts=0;
        const gate=new Promise((resolve)=>{release=resolve;});
        api.get=async()=>{reads++;if(reads===1)await gate;return {limits:{max_files:10}};};
        api.upload=async(path,form)=>{posts++;return response(form.getAll('files'));};
        choose(files(1,'old'));const old=upload.prepare();const rejected=assert.rejects(old);await turn();
        choose(files(2,'new'));assert.deepEqual(await upload.prepare(),['/uploads/new0.png','/uploads/new1.png']);
        release();await rejected;assert.equal(posts,1);assert.deepEqual(upload.value(),['/uploads/new0.png','/uploads/new1.png']);
        """)

    def test_dynamic_selection_limit_is_checked_before_any_upload_or_limit_query(self):
        self.run_script("""
        let limit=16;const {upload,choose}=widget(()=>limit);let reads=0;let posts=0;
        api.get=async()=>{reads++;return {limits:{max_files:10}};};
        api.upload=async(path,form)=>{posts++;return response(form.getAll('files'));};
        choose(files(16));assert.equal(upload.hasPendingFile(),true);limit=10;
        await assert.rejects(upload.prepare());assert.equal(reads,0);assert.equal(posts,0);
        choose(files(11));assert.equal(upload.hasPendingFile(),false);assert.deepEqual(await upload.prepare(),[]);
        choose(files(10));assert.equal((await upload.prepare()).length,10);assert.equal(posts,1);
        """)

    def test_invalid_backend_capacity_does_not_guess_or_start_post(self):
        self.run_script("""
        const {upload,choose}=widget();let posts=0;choose(files(2));
        api.upload=async()=>{posts++;throw new Error('must not post');};
        for(const value of [undefined,0,-1,1.5,'bad']) {
          api.get=async()=>({limits:{max_files:value}});await assert.rejects(upload.prepare());
          assert.deepEqual(upload.value(),[]);
        }
        assert.equal(posts,0);
        """)

    def test_existing_operation_controls_follow_flare_lite_and_pro_catalog_limits(self):
        models={item['id']:item for item in catalog_api_response(load_provider_catalog())['models']}
        self.run_script("""
        const {flare,lite,pro}=JSON.parse(fs.readFileSync(0,'utf8'));
        const {createOperationControls}=await import('./studio/features/generate-image/operation-controls.js');
        const target=new FakeElement('div');const controls=createOperationControls({target,referenceAssets:[]});let posts=0;
        api.get=async()=>({limits:{max_files:10}});
        api.upload=async(path,form)=>{posts++;return response(form.getAll('files'));};
        function selectModel(model,count) {
          controls.sync(model);const operation=walk(target,(node)=>node.name==='operation_mode');
          operation.value='image_edit';operation.listeners.change();
          const input=walk(target,(node)=>node.type==='file' && node.multiple);input.files=files(count);input.listeners.change();
        }
        selectModel(flare,16);assert.equal((await controls.prepare()).reference_images.length,16);assert.equal(posts,2);
        selectModel(lite,14);assert.equal((await controls.prepare()).reference_images.length,14);assert.equal(posts,4);
        selectModel(pro,11);await assert.rejects(controls.prepare(),error=>error.safe?.code==='image_reference_required');assert.equal(posts,4);
        selectModel(pro,10);assert.equal((await controls.prepare()).reference_images.length,10);assert.equal(posts,5);
        """,{'flare':models['openai-flare'],'lite':models['seedream-5-lite'],'pro':models['seedream-5-pro']})
