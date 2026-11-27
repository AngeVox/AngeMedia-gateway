"""Web Studio provider channel UI contracts."""
from __future__ import annotations

import unittest
from pathlib import Path
from test_web_studio_generate_image_operation_controls import run_studio_module_script

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "app" / "www" / "assets" / "studio"

DOM = r"""
import assert from 'node:assert/strict';
class Node {
  constructor(tag) {
    this.tagName=tag;this.children=[];this.dataset={};this.className='';
    this.value='';this.hidden=false;this.open=false;this.listeners={};
    this.classList={add:(name)=>this.classList.toggle(name,true),toggle:(name,enabled)=>{
      const names=new Set(this.className.split(' ').filter(Boolean));
      if(enabled)names.add(name);else names.delete(name);this.className=[...names].join(' ');
    }};
  }
  appendChild(node){this.children.push(node);return node;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  removeEventListener(name,fn){if(this.listeners[name]===fn)delete this.listeners[name];}
  setAttribute(name,value){this[name]=String(value);}
  set textContent(value){this.children=[];this.text=String(value);}
  get textContent(){return (this.text||'')+this.children.map(n=>n.textContent).join('');}
}
globalThis.document={documentElement:{lang:''},createElement:tag=>new Node(tag),createTextNode:text=>{
  const node=new Node('#text');node.textContent=text;return node;
}};
globalThis.location={hash:'#/providers'};
globalThis.window={addEventListener(){},removeEventListener(){}};
globalThis.fetch=()=>{throw new Error('不应产生HTTP请求');};
const walk=(node,predicate)=>[...(predicate(node)?[node]:[]),...node.children.flatMap(n=>walk(n,predicate))];
const hasClass=(node,name)=>node.className.split(' ').includes(name);
"""


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


class WebStudioProviderChannelsContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builtin = read(STUDIO / "features" / "providers" / "builtin-config.js")
        cls.i18n = read(STUDIO / "i18n.js")

    def test_builtin_channels_have_image_video_filters(self) -> None:
        self.assertIn("builtinMediaFilter", self.builtin)
        self.assertIn("providers.imageChannels", self.builtin)
        self.assertIn("providers.videoChannels", self.builtin)
        self.assertIn("mediaTypes.includes(builtinMediaFilter)", self.builtin)

    def test_builtin_type_media_uses_user_facing_labels(self) -> None:
        self.assertIn("providerMediaLabel", self.builtin)
        self.assertIn("providers.builtinImageChannel", self.i18n)
        self.assertIn("providers.builtinVideoChannel", self.i18n)
        self.assertNotIn("provider_type || '-' } ·", self.builtin)

    def test_media_and_search_filters_reuse_rows_without_reload_or_http(self) -> None:
        result = run_studio_module_script(DOM + r"""
const {renderBuiltinConfigPanel}=await import('./studio/features/providers/builtin-config.js');
const {setLanguage}=await import('./studio/i18n.js');setLanguage('zh-CN');
let reloads=0;
const panel=renderBuiltinConfigPanel([
  {provider_id:'alpha',display_name:'Alpha',media_types:['image'],enabled:true},
  {provider_id:'beta',display_name:'Beta',media_types:['video'],enabled:true},
  {provider_id:'mixed',display_name:'Mixed media',media_types:['image','video'],enabled:true},
],()=>{reloads++;},{models:[]});
const rows=walk(panel,n=>hasClass(n,'builtin-provider-row'));
const search=walk(panel,n=>n.type==='search')[0];
const buttons=walk(panel,n=>n.tagName==='button'&&['图片渠道','视频渠道'].includes(n.textContent));
assert.equal(panel.open,true);assert.equal(rows[0].hidden,false);assert.equal(rows[1].hidden,true);
search.value='alpha';search.listeners.input();
buttons[1].listeners.click();
assert.ok(rows.every(n=>n.hidden));
assert.equal(buttons[1]['aria-pressed'],'true');assert.equal(buttons[0]['aria-pressed'],'false');
search.value='BETA';search.listeners.input();
assert.equal(rows[1].hidden,false);assert.equal(rows[0].hidden,true);
buttons[0].listeners.click();assert.ok(rows.every(n=>n.hidden));
search.value='';search.listeners.input();
assert.equal(rows[0].hidden,false);
assert.equal(rows[2].hidden,false);
const media=walk(rows[2],n=>hasClass(n,'builtin-provider-type'))[0].textContent;
assert.ok(media.includes('图片')&&media.includes('视频'));
buttons[1].listeners.click();
assert.equal(rows[2].hidden,false);
assert.deepEqual(walk(panel,n=>hasClass(n,'builtin-provider-row')),rows);
assert.equal(reloads,0);assert.equal(panel.open,true);
console.log(JSON.stringify({ok:true}));
""", {})
        self.assertTrue(result['ok'])

    def test_model_directory_is_provider_scoped_and_localized_in_both_languages(self) -> None:
        result = run_studio_module_script(DOM + r"""
const {renderProviderModels}=await import('./studio/features/providers/catalog-sections.js');
const {setLanguage}=await import('./studio/i18n.js');
for(const lang of ['zh-CN','en-US']){
  setLanguage(lang);
  const panel=renderProviderModels({models:[
    {provider_id:'alpha',display_name:'Model A',selectable:true,capabilities:{text_to_image:true,image_edit:true}},
    {provider_id:'alpha',display_name:'Retired',selectable:true,policy:{lifecycle:'retired'},capabilities:{}},
    {provider_id:'beta',display_name:'Other provider',selectable:true,capabilities:{}},
  ]},'alpha');
  assert.equal(panel.open,false);assert.ok(panel.textContent.includes('(2)')||panel.textContent.includes('（2）'));
  assert.ok(panel.textContent.includes('Model A'));assert.ok(!panel.textContent.includes('Other provider'));
  assert.ok(panel.textContent.includes(lang==='zh-CN'?'文生图':'Text to image'));
  assert.ok(panel.textContent.includes(lang==='zh-CN'?'编辑图片':'Edit image'));
  assert.ok(panel.textContent.includes(lang==='zh-CN'?'目录暂未开放选择':'Selection unavailable in the catalog'));
  assert.ok(!panel.textContent.includes('providers.model'));
}
console.log(JSON.stringify({ok:true}));
""", {})
        self.assertTrue(result['ok'])

    def test_unloaded_directory_is_distinct_from_loaded_empty_directory(self) -> None:
        result = run_studio_module_script(DOM + r"""
const {renderProviderModels}=await import('./studio/features/providers/catalog-sections.js');
const {setLanguage}=await import('./studio/i18n.js');
setLanguage('en-US');
assert.ok(renderProviderModels({},'alpha').textContent.includes('did not load'));
assert.ok(renderProviderModels({models:[]},'alpha').textContent.includes('No models are registered'));
console.log(JSON.stringify({ok:true}));
""", {})
        self.assertTrue(result['ok'])


if __name__ == "__main__":
    unittest.main()
