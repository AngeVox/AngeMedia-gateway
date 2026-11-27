"""任务结果在缓存往返后的读取、订阅与旧回调归属运行回归。"""
import unittest

from test_web_job_result_tracker import SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


RESTORE = SETUP + r"""
const transition=(name,persisted=true)=>[...(listeners.get(name)||[])].forEach(fn=>fn({persisted}));
const {clearSession}=await import('./studio/auth.js');
const {t}=await import('./studio/i18n.js');
"""


class JobResultRestoreRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(RESTORE + script + '\nconsole.log(JSON.stringify({ok:true}));', {})['ok'])

    def test_restore_reads_once_before_reconnecting_known_unfinished_job(self):
        self.run_case(r"""
let reads=0;const held=deferred();api.get=()=>++reads===1?Promise.resolve({data:snapshot()}):held.promise;
const tracker=start();await flush();transition('pagehide');assert.equal(sources[0].closed,true);
await tracker.refresh();assert.equal(reads,1);transition('pageshow');await flush();assert.equal(reads,2);assert.equal(sources.length,1);
held.resolve({data:snapshot()});await flush();assert.equal(sources.length,2);assert.equal(sources[1].closed,false);
await tracker.refresh();assert.equal(reads,3);tracker.close();
""")

    def test_terminal_or_missing_restored_snapshot_does_not_open_another_stream(self):
        self.run_case(r"""
for(const value of [snapshot('succeeded',{assets:[asset]}),snapshot('failed'),snapshot('canceled'),null]){
 let reads=0;api.get=async()=>({data:++reads===1?snapshot():value});const before=sources.length;
 const tracker=start();await flush();transition('pagehide');transition('pageshow');await flush();
 assert.equal(reads,2);assert.equal(sources.length,before+1);assert.equal(sources.at(-1).closed,true);
 assert.equal(walk(target,n=>n.tagName==='img').length,value?.status==='succeeded'?1:0);tracker.close();
}
""")

    def test_success_without_safe_asset_keeps_restored_stream_until_asset_arrives(self):
        self.run_case(r"""
let reads=0;api.get=async()=>({data:++reads===1?snapshot():snapshot('succeeded')});const tracker=start();await flush();
transition('pagehide');transition('pageshow');await flush();assert.equal(sources.length,2);assert.equal(sources[1].closed,false);
assert.equal(target.querySelectorAll('[data-job-refresh]').length,1);sources[1].emit(snapshot('succeeded',{assets:[asset]}));
assert.equal(sources[1].closed,true);assert.equal(walk(target,n=>n.tagName==='img').length,1);tracker.close();
""")

    def test_old_read_success_or_error_cannot_overwrite_or_unlock_new_restore_read(self):
        self.run_case(r"""
for(const fails of [false,true]){
 const old=deferred(),fresh=deferred();let reads=0;api.get=()=>++reads===1?old.promise:fresh.promise;
 const tracker=start();await flush();transition('pagehide');transition('pageshow');await flush();assert.equal(reads,2);
 if(fails)old.reject(new Error('old private failure'));else old.resolve({data:snapshot('succeeded',{assets:[asset]})});await flush();
 assert.equal(target.querySelectorAll('[data-job-refresh]')[0].disabled,true);assert.equal(walk(target,n=>n.tagName==='img').length,0);
 assert.ok(!target.textContent.includes('old private failure'));const shared=tracker.refresh();assert.equal(reads,2);
 fresh.resolve({data:snapshot()});await shared;await flush();assert.equal(target.querySelectorAll('[data-job-refresh]')[0].disabled,false);tracker.close();
}
""")

    def test_old_stream_frames_and_errors_do_not_touch_restored_subscription(self):
        self.run_case(r"""
let reads=0;api.get=async()=>{reads++;return {data:snapshot()};};const tracker=start();await flush();
const old=sources[0];transition('pagehide');old.emit(snapshot('succeeded',{assets:[asset]}));old.error();
transition('pageshow');await flush();const current=sources[1],before=reads;
old.emit(snapshot('succeeded',{assets:[asset]}));old.listeners.job({data:'broken'});old.error();await flush();
assert.equal(reads,before);assert.equal(current.closed,false);assert.equal(walk(target,n=>n.tagName==='img').length,0);
current.emit(snapshot('succeeded',{assets:[asset]}));assert.equal(walk(target,n=>n.tagName==='img').length,1);tracker.close();
""")

    def test_recovery_queued_before_suspension_cannot_issue_extra_restore_read(self):
        self.run_case(r"""
const old=deferred();let reads=0;api.get=()=>++reads===1?old.promise:Promise.resolve({data:snapshot()});
const tracker=start();await flush();sources[0].emit(snapshot());sources[0].error();assert.equal(reads,1);
transition('pagehide');transition('pageshow');await flush();assert.equal(reads,2);
old.resolve({data:snapshot('queued')});await flush();assert.equal(reads,2);assert.equal(sources.length,2);tracker.close();
""")

    def test_repeated_transitions_and_manual_clicks_share_one_restore_read(self):
        self.run_case(r"""
const held=deferred();let reads=0;api.get=()=>++reads===1?Promise.resolve({data:snapshot()}):held.promise;
const tracker=start();await flush();transition('pagehide');transition('pagehide');transition('pageshow');transition('pageshow');await flush();
const first=tracker.refresh(),second=tracker.refresh();assert.equal(first,second);assert.equal(reads,2);
held.resolve({data:snapshot()});await first;await flush();assert.equal(sources.length,2);tracker.close();
""")

    def test_route_removed_owner_and_session_change_while_suspended_prevent_restore(self):
        self.run_case(r"""
for(const boundary of ['route','remove','session','close']){
 location.hash='#/generate/image';let reads=0;api.get=async()=>{reads++;return {data:snapshot()};};const before=sources.length;
 const tracker=start();await flush();transition('pagehide');
 if(boundary==='route'){route('#/jobs');route('#/generate/image');}else if(boundary==='remove'){target.remove();observers.at(-1).callback();content.append(target);}
 else if(boundary==='session')clearSession();else tracker.close();
 transition('pageshow');await tracker.refresh();await flush();assert.equal(reads,1);assert.equal(sources.length,before+1);
 assert.equal(observers.at(-1).disconnected,true);assert.equal(listeners.get('pageshow').size,0);tracker.close();
}
""")

    def test_same_target_replacement_and_old_close_never_stop_new_tracker(self):
        self.run_case(r"""
api.get=async()=>({data:snapshot()});const old=start();await flush();transition('pagehide');
const current=start();await flush();const source=sources.at(-1);old.close();transition('pageshow');await flush();
assert.equal(sources.length,2);assert.equal(source.closed,false);current.close();
for(const name of ['hashchange','pagehide','pageshow'])assert.equal(listeners.get(name).size,0);
""")

    def test_restore_read_failure_keeps_known_fact_and_manual_retry(self):
        self.run_case(r"""
for(const finished of [false,true]){
 const initial=finished?snapshot('succeeded',{assets:[asset]}):snapshot();let reads=0;
 api.get=async()=>{if(++reads===2)throw new Error('private transport failure');return {data:initial};};
 const tracker=start(initial);await flush();transition('pagehide');transition('pageshow');await flush();
 assert.equal(walk(target,n=>n.role==='alert').length,1);assert.ok(!target.textContent.includes('private transport failure'));
 const retry=target.querySelectorAll('[data-job-refresh]')[0];assert.ok(retry);assert.equal(retry.disabled,false);
 await tracker.refresh();assert.equal(reads,3);assert.equal(walk(target,n=>n.role==='alert').length,0);
 assert.equal(walk(target,n=>n.tagName==='img').length,finished?1:0);tracker.close();
}
""")

    def test_restore_without_eventsource_stays_bounded_and_manual_refresh_works(self):
        self.run_case(r"""
globalThis.EventSource=undefined;let reads=0;api.get=async()=>{reads++;return {data:snapshot()};};
const tracker=start();await flush();transition('pagehide');transition('pageshow');await flush();assert.equal(reads,2);
assert.ok(target.textContent.includes(t('generateResult.connectionLost')));await tracker.refresh();assert.equal(reads,3);tracker.close();
""")

    def test_close_during_restore_read_prevents_reconnect_and_late_paint(self):
        self.run_case(r"""
const held=deferred();let reads=0;api.get=()=>++reads===1?Promise.resolve({data:snapshot()}):held.promise;
const tracker=start();await flush();transition('pagehide');transition('pageshow');await flush();tracker.close();target.textContent='New view';
held.resolve({data:snapshot('succeeded',{assets:[asset]})});await flush();assert.equal(target.textContent,'New view');assert.equal(sources.length,1);
assert.equal(target['aria-busy'],undefined);
""")

    def test_session_change_releases_live_stream_on_callback_without_dom_mutation(self):
        self.run_case(r"""
for(const callback of ['event','refresh']){
 api.get=async()=>({data:snapshot()});const tracker=start();await flush();const source=sources.at(-1),observer=observers.at(-1);
 clearSession();if(callback==='event')source.emit(snapshot('succeeded',{assets:[asset]}));else await tracker.refresh();
 assert.equal(source.closed,true);assert.equal(observer.disconnected,true);assert.equal(walk(target,n=>n.tagName==='img').length,0);
 assert.equal(listeners.get('pageshow').size,0);tracker.close();
}
""")
