"""高级渠道设置的读取、异步写入和会话归属回归。"""
import unittest

from test_web_provider_forms_runtime import SETUP
from test_web_studio_generate_image_operation_controls import run_studio_module_script


ADVANCED = SETUP + r"""
const {renderConnectivitySettingsPanel}=await import('./studio/features/providers/connectivity-settings.js');
const {renderRuntimeSettingsPanel}=await import('./studio/features/providers/runtime-settings.js');
const proxyData={data:{transport_mode:'explicit_proxy',effective_mode:'explicit_proxy',proxy_configured:true}};
const relayData={data:{mode:'external_http',upload_url_configured:true,token_configured:true}};
const runtimeData={settings:{HTTP_TIMEOUT:'61.5',AGNES_VIDEO_SUBMIT_TIMEOUT:'925'}};
const make=()=>{const section=renderConnectivitySettingsPanel({onTransportSaved:reload});content.append(section);return section;};
const proxySection=node=>walk(node,n=>hasClass(n,'provider-connectivity-block'))[0];
const buttonBy=(node,key)=>controls(node).find(n=>n.textContent===t(key));
"""


class AdvancedProviderSettingsTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(
            ADVANCED + script + "\nconsole.log(JSON.stringify({ok:true}));", {}
        )["ok"])

    def test_resumed_form_discards_suspended_read_and_clears_secrets(self):
        self.run_case(r"""
const old=deferred(),restored=deferred();let reads=0,writes=0;
api.get=path=>path.endsWith('provider-transport')?(++reads===1?old.promise:restored.promise):Promise.resolve(relayData);
api.post=async()=>{writes++;return proxyData;};
const panel=make(),proxy=proxySection(panel);await flush();
fields(proxy)[0].value='fixture-autofill';
for(const fn of [...listeners.get('pagehide')])fn({persisted:true});
assert.equal(fields(proxy)[0].value,'');
for(const fn of [...listeners.get('pageshow')])fn({persisted:true});
assert.equal(reads,2);
restored.resolve(proxyData);await flush();
old.resolve({data:{transport_mode:'direct',effective_mode:'direct'}});await flush();
assert.equal(proxy.querySelector('select').value,'explicit_proxy');
assert.equal(buttonBy(proxy,'providers.transportSave').disabled,false);assert.equal(writes,0);
""")

    def test_old_session_does_not_block_new_timeout_save_or_publish_late_feedback(self):
        self.run_case(r"""
api.get=async()=>runtimeData;
const oldAck=deferred(),newAck=deferred();let writes=0;
api.post=(path,body)=>path==='/admin/login'?Promise.resolve({username:'new'}):(++writes===1?oldAck.promise:newAck.promise);
const timeout=()=>{const node=renderRuntimeSettingsPanel();content.append(node);for(const input of fields(node))input.reportValidity=()=>true;return node;};
const old=timeout();await flush();const oldSave=click(old,'providers.runtimeTimeoutSave');
await auth.login('new','fixture-password');
old.remove();const current=timeout();await flush();
assert.equal(fields(current)[0].value,'61.5');assert.equal(buttonBy(current,'providers.runtimeTimeoutSave').disabled,false);
fields(current)[0].value='70';const newSave=click(current,'providers.runtimeTimeoutSave');
const toastCount=toasts.children.length;
oldAck.resolve({settings:{IMAGE_PROVIDER_TIMEOUT:'50'}});await oldSave;
assert.equal(toasts.children.length,toastCount);assert.equal(buttonBy(current,'providers.runtimeTimeoutSave').disabled,true);
await click(current,'providers.runtimeTimeoutSave');assert.equal(writes,2);
newAck.resolve({settings:{IMAGE_PROVIDER_TIMEOUT:'70',VIDEO_PROVIDER_TIMEOUT:'925'}});await newSave;
assert.equal(fields(current)[0].value,'70');assert.equal(toasts.children.length,toastCount+1);
""")

    def test_malformed_load_and_failed_write_require_a_deliberate_retry(self):
        self.run_case(r"""
let reads=0,writes=[];
api.get=async path=>path.endsWith('provider-transport')?(++reads===1?{}:proxyData):relayData;
api.post=async(path,body)=>{writes.push(body);throw new Error('fixture failure');};
const proxy=proxySection(make());await flush();
await click(proxy,'providers.transportSave');assert.equal(writes.length,0);
await click(proxy,'common.refresh');assert.equal(reads,2);
fields(proxy)[0].value='http://fixture.invalid:1234';
await click(proxy,'providers.transportSave');
assert.equal(fields(proxy)[0].value,'');assert.equal(proxy.querySelector('select').value,'explicit_proxy');
assert.deepEqual(writes,[{transport_mode:'explicit_proxy',proxy_url:'http://fixture.invalid:1234'}]);
assert.equal(reloads,0);assert.equal(buttonBy(proxy,'providers.transportSave').disabled,false);
""")
