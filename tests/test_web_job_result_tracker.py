"""原结果跟踪器的事件、快照与视图所有权；浏览器另验真实SSE及创作入口。"""
import unittest

from test_web_render_lifecycle import SETUP as VIEW_SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


SETUP = VIEW_SETUP + r"""
location.hash='#/generate/image';
window.location={origin:'https://gateway.example'};
Node.prototype.querySelectorAll=function(selector){
 if(selector==='[data-job-refresh]')return walk(this,n=>n.dataset.jobRefresh);
 return walk(this,n=>selector.startsWith('.')?hasClass(n,selector.slice(1)):selector.split(',').includes(n.tagName));
};
const observers=[];globalThis.MutationObserver=class {
 constructor(callback){this.callback=callback;this.disconnected=false;observers.push(this);}
 observe(){} disconnect(){this.disconnected=true;}
};
const sources=[];globalThis.EventSource=class {
 constructor(url){this.url=url;this.listeners={};this.closed=false;sources.push(this);}
 addEventListener(type,callback){this.listeners[type]=callback;}
 close(){this.closed=true;}
 emit(detail){this.listeners.job({data:JSON.stringify({data:detail})});}
 error(){this.listeners.error();}
};
const {startJobResultTracker,stopJobResultTracker}=await import('./studio/components/job-result-tracker.js?v=web-studio-2j');
const target=new Node('section');content.append(target);
const snapshot=(status='running',extra={})=>({job_id:'fixture-job',status,prompt_summary:'Fixture prompt',...extra});
const asset={media_type:'image',url_path:'/generated/fixture.png'};
const start=initial=>startJobResultTracker(target,{jobId:'fixture-job',mediaType:'image',initial:initial||snapshot('queued')});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
"""


class JobResultTrackerRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(
            SETUP + script + "\nconsole.log(JSON.stringify({ok:true}));", {}
        )["ok"])

    def test_stream_error_waits_for_obsolete_read_then_fetches_usable_snapshot(self):
        self.run_case(r"""
const first=deferred();let calls=0;
api.get=()=>++calls===1?first.promise:Promise.resolve({data:snapshot('succeeded',{assets:[asset]})});
const tracker=start();await flush();sources[0].emit(snapshot());sources[0].error();
assert.equal(sources[0].closed,true);assert.equal(calls,1);
first.resolve({data:snapshot('queued')});await flush();
assert.equal(calls,2);assert.equal(walk(target,n=>n.tagName==='img').length,1);
assert.ok(!target.textContent.includes('queued'));tracker.close();
""")

    def test_snapshot_cannot_regress_a_newer_stream_event(self):
        self.run_case(r"""
const pending=deferred();api.get=()=>pending.promise;const tracker=start();await flush();
sources[0].emit(snapshot('succeeded',{assets:[asset]}));assert.equal(sources[0].closed,true);
pending.resolve({data:snapshot('running')});await flush();
assert.equal(walk(target,n=>n.tagName==='img').length,1);tracker.close();
""")

    def test_native_connecting_stream_is_kept_and_next_event_clears_connection_notice(self):
        self.run_case(r"""
api.get=async()=>({data:snapshot()});const tracker=start();await flush();
sources[0].readyState=0;sources[0].error();await flush();
assert.equal(sources[0].closed,false);assert.ok(target.textContent.includes('Reconnecting to live progress'));
sources[0].readyState=1;sources[0].emit(snapshot('succeeded',{assets:[asset]}));
assert.equal(sources[0].closed,true);assert.ok(!target.textContent.includes('Reconnecting to live progress'));
assert.equal(walk(target,n=>n.tagName==='img').length,1);tracker.close();
""")

    def test_succeeded_without_asset_keeps_stream_and_manual_refresh_until_asset_arrives(self):
        self.run_case(r"""
api.get=async()=>({data:snapshot()});const tracker=start();await flush();
sources[0].emit(snapshot('succeeded'));assert.equal(sources[0].closed,false);
assert.equal(target.querySelectorAll('[data-job-refresh]').length,1);
sources[0].emit(snapshot('succeeded',{assets:[asset]}));assert.equal(sources[0].closed,true);
assert.equal(walk(target,n=>n.tagName==='img').length,1);tracker.close();
""")

    def test_refresh_error_preserves_last_state_and_explicit_retry_is_available(self):
        self.run_case(r"""
let calls=0;api.get=async()=>{calls++;throw new Error('private request body');};
const tracker=start(snapshot());await flush();
assert.ok(target.textContent.includes('Running'));assert.ok(!target.textContent.includes('private request body'));
assert.equal(target.querySelectorAll('[data-job-refresh]')[0].disabled,false);assert.equal(calls,1);
api.get=async()=>({data:snapshot('succeeded',{assets:[asset]})});await tracker.refresh();
assert.equal(walk(target,n=>n.tagName==='img').length,1);assert.equal(walk(target,n=>n.role==='alert').length,0);tracker.close();
""")

    def test_missing_or_unconstructable_eventsource_uses_bounded_read_and_manual_retry(self):
        self.run_case(r"""
for(const Source of [undefined,class {constructor(){throw new Error('not available');}}]){
 globalThis.EventSource=Source;let calls=0;api.get=async()=>{calls++;return {data:snapshot()};};
 const tracker=start();await flush();assert.equal(calls,1);assert.ok(target.textContent.includes('Live updates are unavailable'));
 await tracker.refresh();assert.equal(calls,2);tracker.close();
}
""")

    def test_replacing_same_target_closes_old_owner_and_drops_old_callbacks(self):
        self.run_case(r"""
const pending=deferred();api.get=()=>pending.promise;const old=start();await flush();
api.get=async()=>({data:snapshot('running',{job_id:'new-job'})});
const current=startJobResultTracker(target,{jobId:'new-job',mediaType:'image',initial:{status:'queued'}});await flush();
assert.equal(sources[0].closed,true);sources[0].emit(snapshot('succeeded',{assets:[asset]}));sources[0].error();
pending.resolve({data:snapshot('succeeded',{assets:[asset]})});await flush();
assert.ok(target.textContent.includes('new-job'));assert.equal(walk(target,n=>n.tagName==='img').length,0);
old.close();assert.equal(sources[1].closed,false);current.close();
""")

    def test_route_pagehide_and_removed_target_release_all_local_observers(self):
        self.run_case(r"""
for(const boundary of ['hashchange','pagehide','remove']){
 api.get=async()=>({data:snapshot()});const tracker=start();await flush();const source=sources.at(-1),observer=observers.at(-1);
 if(boundary==='remove'){target.remove();observer.callback();}else if(boundary==='hashchange')route('#/jobs');else [...listeners.get(boundary)].forEach(fn=>fn());
 assert.equal(source.closed,true);assert.equal(observer.disconnected,true);
 assert.equal(listeners.get('hashchange').size,0);assert.equal(listeners.get('pagehide').size,0);
 let calls=0;api.get=async()=>{calls++;return {data:snapshot()};};await tracker.refresh();assert.equal(calls,0);
 if(boundary==='remove')content.append(target);location.hash='#/generate/image';
}
""")

    def test_late_read_error_after_close_does_not_replace_current_view(self):
        self.run_case(r"""
const pending=deferred();api.get=()=>pending.promise;const tracker=start();await flush();
tracker.close();target.textContent='New view';pending.reject(new Error('late failure'));await flush();
assert.equal(target.textContent,'New view');assert.equal(target['aria-busy'],undefined);
""")

    def test_repeated_manual_refresh_shares_one_inflight_read(self):
        self.run_case(r"""
let calls=0;const pending=deferred();api.get=()=>{calls++;return pending.promise;};
const tracker=start();const first=tracker.refresh(),second=tracker.refresh();assert.equal(first,second);await flush();
assert.equal(calls,1);assert.equal(target.querySelectorAll('[data-job-refresh]')[0].disabled,true);
pending.resolve({data:snapshot()});await first;
assert.equal(target.querySelectorAll('[data-job-refresh]')[0].disabled,false);tracker.close();
""")

    def test_invalid_frame_and_wrong_job_snapshot_never_apply_unrelated_results(self):
        self.run_case(r"""
api.get=async()=>({data:snapshot('succeeded',{job_id:'wrong-job',assets:[asset]})});
const tracker=start(snapshot());await flush();assert.equal(walk(target,n=>n.tagName==='img').length,0);
sources[0].listeners.job({data:'malformed JSON'});await flush();assert.equal(sources[0].closed,true);
assert.ok(target.textContent.includes('Progress could not be updated'));assert.equal(walk(target,n=>n.tagName==='img').length,0);tracker.close();
""")

    def test_canceled_job_is_not_mislabeled_failed_and_refresh_stays_usable(self):
        self.run_case(r"""
api.get=async()=>({data:snapshot('canceled')});const tracker=start(snapshot('canceled'));await flush();
assert.equal(sources.length,0);assert.ok(target.textContent.includes('Canceled'));assert.ok(!target.textContent.includes('Job failed'));
assert.equal(target.querySelectorAll('[data-job-refresh]')[0].disabled,false);await tracker.refresh();tracker.close();
""")

    def test_start_on_detached_target_and_close_before_read_start_issue_no_requests(self):
        self.run_case(r"""
let calls=0;api.get=async()=>{calls++;return {data:snapshot()};};
target.remove();assert.equal(start(),null);assert.equal(sources.length,0);content.append(target);
const tracker=start();tracker.close();await flush();assert.equal(calls,0);
""")

    def test_original_result_loading_stops_previous_tracker_before_replacing_ui(self):
        self.run_case(r"""
api.get=async()=>({data:snapshot()});const tracker=start();await flush();
const {renderResultLoading}=await import('./studio/features/generate-image/result-preview.js');renderResultLoading(target);
assert.equal(sources[0].closed,true);assert.equal(observers[0].disconnected,true);
const currentText=target.textContent;sources[0].emit(snapshot('succeeded',{assets:[asset]}));await flush();
assert.equal(target.textContent,currentText);tracker.close();stopJobResultTracker(target);
""")
