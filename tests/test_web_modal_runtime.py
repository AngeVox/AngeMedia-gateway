"""公共弹窗的提交、关闭与滚动所有权合同；原生交互另用浏览器验收。"""
import unittest

from test_web_studio_generate_image_operation_controls import run_studio_module_script
from test_web_studio_provider_channels import DOM

SETUP = DOM + r"""
const mark=(node,value)=>{node.isConnected=value;node.children.forEach(n=>mark(n,value));};
Node.prototype.appendChild=function(node){this.children.push(node);node.parent=this;mark(node,!!this.isConnected);return node;};
Node.prototype.append=function(...nodes){nodes.forEach(n=>this.appendChild(n));};
Node.prototype.remove=function(){this.parent.children=this.parent.children.filter(n=>n!==this);mark(this,false);};
Node.prototype.querySelectorAll=function(selector){return walk(this,n=>selector.split(',').includes(n.tagName));};
Node.prototype.querySelector=function(selector){return this.querySelectorAll(selector)[0];};
Node.prototype.focus=function(){document.activeElement=this;};
Node.prototype.showModal=function(){this.open=true;};
Node.prototype.close=function(){this.open=false;};
Node.prototype.removeAttribute=function(name){delete this[name];};
document.body=new Node('body');document.body.style={overflow:'clip'};document.body.isConnected=true;
const trigger=new Node('button');document.body.append(trigger);trigger.focus();
const listeners=new Map();globalThis.window={
 addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn);},
 removeEventListener(name,fn){listeners.get(name)?.delete(fn);}
};
const {confirmModal,doubleConfirmModal,lockModalScroll}=await import('./studio/components/modal.js');
const {setLanguage}=await import('./studio/i18n.js');setLanguage('en-US');
const controls=overlay=>walk(overlay,n=>n.tagName==='button');
const options={title:'Fixture',message:'Fixture only',confirmLabel:'Proceed',cancelLabel:'Cancel'};
"""


class ModalRuntimeTest(unittest.TestCase):
    def run_case(self, script):
        self.assertTrue(run_studio_module_script(SETUP + script + "\nconsole.log(JSON.stringify({ok:true}));", {})['ok'])

    def test_scroll_ownership_is_idempotent_and_independent_of_close_order(self):
        self.run_case(r"""
const first=lockModalScroll(),second=lockModalScroll();first();first();
assert.equal(document.body.style.overflow,'hidden');second();assert.equal(document.body.style.overflow,'clip');
""")

    def test_confirmation_allows_only_one_pending_callback(self):
        self.run_case(r"""
let release,calls=0;
const overlay=confirmModal({...options,onConfirm:()=>{calls++;return new Promise(resolve=>{release=resolve;});}});
const [cancel,confirm]=controls(overlay);
const pending=confirm.listeners.click();await confirm.listeners.click();
assert.equal(calls,1);assert.equal(confirm.disabled,true);assert.equal(cancel.textContent,'Close');
release();await pending;assert.equal(overlay.isConnected,false);assert.equal(document.body.style.overflow,'clip');
assert.equal(document.activeElement,trigger);
""")

    def test_failed_callback_stays_open_and_never_retries_without_a_click(self):
        self.run_case(r"""
let calls=0;
const overlay=confirmModal({...options,onConfirm:async()=>{calls++;if(calls===1)throw new Error('private details');}});
const confirm=controls(overlay)[1];await confirm.listeners.click();
assert.equal(calls,1);assert.equal(overlay.isConnected,true);assert.equal(confirm.disabled,false);
const error=walk(overlay,n=>n.role==='alert')[0];assert.equal(error.hidden,false);assert.ok(!error.textContent.includes('private details'));
await confirm.listeners.click();assert.equal(calls,2);assert.equal(overlay.isConnected,false);
""")

    def test_double_confirmation_keeps_phrase_requirement_and_busy_guard(self):
        self.run_case(r"""
let release,calls=0;
const overlay=doubleConfirmModal({...options,secondMessage:'Second stage',confirmText:'CLEAN',onConfirm:()=>{calls++;return new Promise(resolve=>{release=resolve;});}});
controls(overlay)[1].listeners.click();
const typed=overlay.querySelector('input'),confirm=controls(overlay)[1];
typed.value='wrong';typed.listeners.input();await confirm.listeners.click();assert.equal(calls,0);
typed.value=' CLEAN ';typed.listeners.input();const pending=confirm.listeners.click();
typed.value='CLEAN';typed.listeners.input();assert.equal(confirm.disabled,true);await confirm.listeners.click();assert.equal(calls,1);
release();await pending;assert.equal(overlay.isConnected,false);
""")

    def test_route_close_invalidates_pending_callback_without_reopening(self):
        self.run_case(r"""
let release,active;
const overlay=confirmModal({...options,onConfirm:async({isActive})=>{await new Promise(resolve=>{release=resolve;});active=isActive();}});
const pending=controls(overlay)[1].listeners.click();
[...listeners.get('hashchange')].forEach(fn=>fn());
assert.equal(overlay.isConnected,false);release();await pending;assert.equal(active,false);
assert.equal(document.body.style.overflow,'clip');assert.equal(listeners.get('hashchange').size,0);
""")
