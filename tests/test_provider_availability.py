"""本地配置投影与创作选择共用合同；不进行连接或生成探测。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ.setdefault('ADMIN_USERNAME', 'admin')
os.environ.setdefault('ADMIN_DEFAULT_PASSWORD', 'admin123456')
os.environ.setdefault('PUBLIC_BASE_URL', 'http://testserver')

from studio_test_client import TestClient
from provider_catalog_support import assert_provider_runtime_projection
from angemedia_gateway import config as C
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.providers.catalog.api import catalog_api_response
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.providers.runtime_config import MODELSCOPE_API_BASE_URL
from angemedia_gateway.repositories.admin_auth import ensure_default_admin_user
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.repositories.settings import upsert_custom_provider
from angemedia_gateway.server import app
from test_web_studio_generate_image_operation_controls import run_studio_module_script


class ProviderAvailabilityTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='provider-availability-')
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name)
        values = {name: '' for name in ('SILICONFLOW_API_KEY', 'MODELSCOPE_API_KEY', 'AGNES_API_KEY',
            'OPENAI_IMAGE_API_KEY', 'BYTEDANCE_API_KEY', 'POLLINATIONS_API_KEY', 'GATEWAY_API_KEY')}
        values.update(DB_FILE=path/'test.db', OUTPUT_DIR=path/'generated', UPLOAD_DIR=path/'uploads')
        for folder in ('generated', 'uploads'): (path/folder).mkdir()
        item = patch.multiple(C, **values); item.start(); self.addCleanup(item.stop)
        init_db(); ensure_default_admin_user()
        self.client = TestClient(app); self.addCleanup(self.client.close)
        result = self.client.post('/v1/admin/login', json={'username': 'admin', 'password': 'admin123456'})
        self.assertEqual(result.status_code, 200, result.text)

    def catalog(self):
        result = self.client.get('/v1/admin/catalog')
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def providers(self): return {p['id']: p for p in self.catalog()['providers']}

    def custom(self):
        return upsert_custom_provider({'id': 'anonymous', 'name': 'Anonymous', 'provider_type': 'openai_image',
            'base_url': 'https://anonymous.example.test/v1', 'default_model': 'test-image', 'api_key': '', 'enabled': True})

    def node(self, script, payload=None):
        return run_studio_module_script(script, payload or {})

    def test_static_catalog_stays_pure_and_runtime_is_admin_only(self):
        self.assertTrue(all('runtime' not in p for p in catalog_api_response(load_provider_catalog())['providers']))
        with TestClient(app) as anonymous:
            self.assertEqual(anonymous.get('/v1/admin/catalog').status_code, 401)

    def test_live_enable_and_credentials_are_independent_of_static_defaults(self):
        for enabled, key in ((True, ''), (True, 'fixture-runtime-secret'), (False, 'fixture-runtime-secret')):
            update_provider_runtime_config('bytedance', enabled=enabled, api_key=key or None)
            with self.subTest(enabled=enabled, key_present=bool(key)):
                p = self.providers()['bytedance']
                self.assertEqual(p['runtime'], {'enabled': enabled, 'api_key_configured': bool(key)})
                self.assertFalse(p['enabled_default'])
                self.assertNotIn('fixture-runtime-secret', json.dumps(p))

    def test_environment_credential_fallback_uses_existing_resolver(self):
        with patch.object(C, 'MODELSCOPE_API_KEY', 'fixture-environment-key'):
            self.assertTrue(self.providers()['modelscope']['runtime']['api_key_configured'])
        self.assertFalse(self.providers()['modelscope']['runtime']['api_key_configured'])

    def test_agnes_video_sees_shared_image_credential_and_disable_independently(self):
        update_provider_runtime_config('agnes_image', enabled=True, api_key='fixture-shared-key')
        update_provider_runtime_config('agnes_video', enabled=False)
        rows = self.providers()
        self.assertTrue(rows['agnes_video']['runtime']['api_key_configured'])
        self.assertFalse(rows['agnes_video']['runtime']['enabled'])
        self.assertTrue(rows['agnes_image']['runtime']['enabled'])

    def test_catalog_does_not_probe_network_or_promote_mock_to_real_provider(self):
        update_provider_runtime_config('modelscope', base_url_override=MODELSCOPE_API_BASE_URL)
        with patch('angemedia_gateway.services.provider_test.provider_client') as network:
            catalog = self.catalog()
        network.assert_not_called()
        mock = next(p for p in catalog['providers'] if p['id'] == 'mock')
        self.assertEqual(mock['runtime'], {'enabled': False, 'api_key_configured': False})
        self.assertTrue(all(not p['policy']['generation_verified'] for p in catalog['providers']))
        for provider in catalog['providers']:
            assert_provider_runtime_projection(self, provider)
        modelscope = next(p for p in catalog['providers'] if p['id'] == 'modelscope')
        directory = modelscope['runtime']['model_directory']
        self.assertEqual(set(directory), {'status', 'evidence_state', 'server_time'})
        self.assertEqual((directory['status'], directory['evidence_state']), ('not_tested', 'not_tested'))

    def test_anonymous_custom_list_detail_and_toggle_share_configuration_projection(self):
        self.custom()
        summary = self.client.get('/v1/admin/providers').json()['data'][0]
        detail = self.client.get('/v1/admin/providers/anonymous').json()['data']
        changed = self.client.post('/v1/admin/providers/anonymous/enabled', json={'enabled': False}).json()['data']
        for row in (summary, detail, changed):
            self.assertTrue(row['configuration_complete']); self.assertFalse(row['api_key_configured'])
        self.assertNotIn('base_url', summary); self.assertFalse(changed['enabled'])

    def test_old_incomplete_custom_rows_are_not_reported_configured(self):
        from angemedia_gateway.services.admin_service import AdminService
        row = AdminService.provider_studio_summary({'id': 'old', 'enabled': True, 'provider_type': 'openai_image'})
        self.assertFalse(row['configuration_complete'])

    def test_javascript_local_configuration_matrix_keeps_unknown_closed(self):
        result = self.node("""
        import {providerAvailability} from './studio/lib/capabilities.js';
        const builtin={id:'builtin',ui_group:'builtin_image',requires_key:true};
        const rows=[builtin,{...builtin,runtime:{enabled:false,api_key_configured:true}},
          {...builtin,runtime:{enabled:true,api_key_configured:false}},
          {...builtin,runtime:{enabled:true,api_key_configured:true}},
          {...builtin,requires_key:false,runtime:{enabled:true,api_key_configured:false}},
          {id:'custom',provider_type:'openai_image',enabled:true,configuration_complete:true,api_key_configured:false},
          {id:'custom',provider_type:'openai_image',enabled:false,configuration_complete:true},
          {...builtin,ui_group:'test',runtime:{enabled:true,api_key_configured:true}},
          {id:'old-custom',provider_type:'openai_image',enabled:true}];
        console.log(JSON.stringify({values:rows.map(providerAvailability)}));
        """)['values']
        self.assertEqual([r['selectable'] for r in result], [False, False, False, True, True, True, False, False, False])
        self.assertEqual(result[0]['reasonKey'], 'availability.unknown')
        self.assertEqual(result[-1]['reasonKey'], 'availability.unknown')

    def test_javascript_inventory_includes_builtin_and_custom_but_not_mock(self):
        result = self.node("""
        import {providerInventory} from './studio/lib/capabilities.js';
        const rows=providerInventory({providers:[{id:'one',ui_group:'builtin_image'},
          {id:'two',ui_group:'builtin_video'},{id:'mock',ui_group:'test'}]},[{id:'one',provider_type:'openai_image'}]);
        console.log(JSON.stringify({ids:rows.map(r=>r.id),empty:providerInventory(null,[])}));
        """)
        self.assertEqual(result['ids'], ['one', 'two', 'one']); self.assertEqual(result['empty'], [])

    def test_javascript_default_candidate_check_does_not_invent_or_reorder_routes(self):
        result = self.node("""
        import {defaultImageRouteReady} from './studio/lib/capabilities.js';
        const provider={id:'p',ui_group:'builtin_image',requires_key:true,runtime:{enabled:true,api_key_configured:true}};
        const model={id:'m',provider_id:'p',media_type:'image',status:'release',selectable:true,default_chain_order:10};
        const check=(p,m)=>defaultImageRouteReady({providers:[p],models:[m]});
        console.log(JSON.stringify({values:[check(provider,model),check({...provider,runtime:null},model),
          check(provider,{...model,default_chain_order:null}),check(provider,{...model,selectable:false})]}));
        """)
        self.assertEqual(result['values'], [True, False, False, False])

    def test_javascript_provider_options_keep_inactive_entries_disabled_in_both_locales(self):
        result = self.node("""
        import {providerOptions} from './studio/features/generate-image/catalog-state.js';
        import {setLanguage} from './studio/i18n.js';
        const active={id:'on',display_name:'Active',ui_group:'builtin_image',requires_key:true,runtime:{enabled:true,api_key_configured:true}};
        const off={...active,id:'off',runtime:{enabled:false,api_key_configured:true}};
        const anon={id:'anon',provider_type:'openai_image',enabled:true,configuration_complete:true,api_key_configured:false};
        const results={};
        for(const locale of ['zh-CN','en-US']){setLanguage(locale);results[locale]=providerOptions([active,off],[anon]);}
        console.log(JSON.stringify(results));
        """)
        for rows in result.values():
            self.assertEqual([r['disabled'] for r in rows], [False, False, True, False])
            self.assertTrue(all('availability.' not in r['label'] for r in rows))
        self.assertEqual(result['zh-CN'][1]['label'], result['en-US'][1]['label'])
        self.assertNotEqual(result['zh-CN'][2]['label'], result['en-US'][2]['label'])

    def test_javascript_custom_selection_requires_endpoint_but_not_api_key(self):
        result = self.node("""
        import {isSelectableImageProvider} from './studio/lib/capabilities.js';
        const p={id:'x',provider_type:'openai_image',enabled:true,configuration_complete:true};
        console.log(JSON.stringify({values:[p,{...p,configuration_complete:false},{...p,provider_type:'video'},
          {...p,id:''}].map(isSelectableImageProvider)}));
        """)
        self.assertEqual(result['values'], [True, False, False, False])
