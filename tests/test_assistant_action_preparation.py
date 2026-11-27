"""准备阶段的能力、私有配置与引用绑定；确认不接受有损正文投影。"""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from assistant_action_support import ActionFixture
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.repositories.settings import set_config, upsert_custom_provider, update_custom_provider_details
from angemedia_gateway.services.assistant_action_service import AssistantActionService
from angemedia_gateway.services.assistant_action_policy import action_policy
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService
from angemedia_gateway.schemas import ImageRequest


class ActionPreparationTest(ActionFixture, unittest.TestCase):
    def custom(self):
        return upsert_custom_provider({'id': 'action-fixture', 'name': '测试渠道', 'base_url': 'https://example.invalid/v1',
            'api_key': 'fixture-custom-credential-123456789', 'default_model': 'model-a'})

    def test_custom_default_is_pinned_without_claiming_verified_capabilities(self):
        self.custom()
        action = self.propose(request={'prompt': '蓝色山谷', 'model': 'custom:action-fixture'})
        self.assertEqual(action['review']['request']['provider_model'], 'model-a')
        self.assertIn('unverified_model', action['review']['required_acknowledgements'])
        update_custom_provider_details('action-fixture', {'default_model': 'model-b'})
        result = self.confirm(action)
        with closing(db_connect()) as conn:
            payload = json.loads(conn.execute('SELECT input_json FROM jobs WHERE id=?', (result['action']['job']['id'],)).fetchone()[0])
        self.assertEqual(payload['request']['provider_model'], 'model-a')
        self.assertEqual(payload['route']['custom_default_model'], 'model-a')

    def test_custom_endpoint_change_invalidates_previous_review(self):
        self.custom()
        action = self.propose(request={'prompt': '蓝色山谷', 'model': 'custom:action-fixture'})
        update_custom_provider_details('action-fixture', {'base_url': 'https://changed.invalid/v1'})
        self.assert_code('action_environment_changed', self.confirm, action)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_builtin_endpoint_change_invalidates_previous_review(self):
        action = self.propose()
        update_provider_runtime_config('siliconflow', base_url_override='https://changed.invalid/v1')
        self.assert_code('action_environment_changed', self.confirm, action)

    def test_unrelated_setting_change_does_not_discard_exact_review(self):
        action = self.propose()
        set_config('ANGE_ASSISTANT_ALLOW_AGNES', 'false')
        self.assertTrue(self.confirm(action)['created'])

    def test_gateway_reference_content_change_requires_new_revision(self):
        path = self.uploads / 'frame.png'
        path.write_bytes(b'\x89PNG\r\n\x1a\nframe-a')
        action = self.propose(kind='video', request={'prompt': '山谷缓慢推进', 'model': 'agnes-video-v2.0', 'image': '/uploads/frame.png'})
        reference = action['review']['references'][0]
        self.assertEqual(reference['kind'], 'gateway_image')
        self.assertEqual(len(reference['sha256']), 64)
        path.write_bytes(b'\x89PNG\r\n\x1a\nframe-b')
        self.assert_code('action_environment_changed', self.confirm, action)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_reference_change_after_hashing_aborts_write(self):
        path = self.uploads / 'frame.png'
        path.write_bytes(b'\x89PNG\r\n\x1a\nframe-a')
        prepare = self.service.preparer.prepare
        def mutate(*args):
            candidate = prepare(*args)
            path.write_bytes(b'\x89PNG\r\n\x1a\nframe-b')
            return candidate
        with patch.object(self.service.preparer, 'prepare', side_effect=mutate):
            self.assert_code('action_environment_changed', self.propose, kind='video',
                request={'prompt': '山谷', 'model': 'agnes-video-v2.0', 'image': '/uploads/frame.png'})
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_modern_video_remote_content_is_explicitly_unverified_and_acknowledged(self):
        action = self.propose(kind='video', request={'prompt': '山谷', 'model': 'agnes-video-v2.5',
            'images': ['https://example.invalid/frame.png']})
        self.assertFalse(action['review']['references'][0]['content_verified'])
        self.assertIn('mutable_remote_reference', action['review']['required_acknowledgements'])
        self.assert_code('action_acknowledgements_required', self.service.confirm, action['id'],
            self.confirmation(action, acknowledgements=['may_incur_cost']), access=self.alice)
        self.assertTrue(self.confirm(action)['created'])

    def test_agnes_administrative_switch_cannot_be_bypassed(self):
        set_config('ANGE_ASSISTANT_ALLOW_AGNES', 'false')
        action = self.propose(kind='video', request={'prompt': '山谷', 'model': 'agnes-video-v2.0'})
        self.assertIn('agnes_generation_disabled', action['review']['blocked_reasons'])
        self.assert_code('action_policy_denied', self.confirm, action)

    def test_known_secret_in_prompt_is_rejected_and_never_saved(self):
        self.assert_code('action_contains_sensitive_value', self.propose, request={'prompt': C.SILICONFLOW_API_KEY, 'model': 'kolors'})
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM assistant_actions').fetchone()[0], 0)

    def test_later_configured_secret_withholds_old_public_review(self):
        action = self.propose(request={'prompt': 'future-private-credential-123456789', 'model': 'kolors'})
        with patch.object(C, 'SILICONFLOW_API_KEY', 'future-private-credential-123456789'):
            view = self.service.inspect(action['id'], access=self.alice)
            self.assertIsNone(view['review']); self.assertTrue(view['review_withheld'])
            self.assert_code('action_contains_sensitive_value', self.confirm, action)

    def test_lossy_prompt_and_signed_reference_are_not_silently_approved(self):
        self.assert_code('action_payload_not_preservable', self.propose, request={'prompt': '景' * 8001, 'model': 'kolors'})
        self.assert_code('action_payload_not_preservable', self.propose, kind='video', request={'prompt': '山谷',
            'model': 'agnes-video-v2.5', 'images': ['https://example.invalid/a.png?token=private']})

    def test_invalid_or_unavailable_route_returns_fixed_codes_without_raw_input(self):
        self.assert_code('invalid_action_request', self.propose, request={'prompt': 'private-prompt', 'model': 'missing-model-selector'})
        self.assert_code('invalid_action_request', self.propose, request={'prompt': 'private-prompt', 'model': 'kolors', 'confirmed': True})
        self.assert_code('action_provider_not_found', self.propose, request={'prompt': 'private-prompt', 'model': 'custom:missing'})

    def test_db_path_scope_is_independent_of_global_default(self):
        action = self.propose()
        other = self.temp / 'unused-default.db'
        with patch.object(C, 'DB_FILE', other):
            self.assertTrue(self.confirm(action)['created'])
        self.assertFalse(other.exists())

    def test_all_fallback_targets_are_reviewed_and_unknown_target_needs_acknowledgement(self):
        arguments = ImageJobAdmissionService().prepare(ImageRequest(prompt='山谷', model='kolors')).arguments()
        arguments['payload']['route']['routes'].append({'provider': 'modelscope', 'model': 'unknown-model'})
        policy, bindings = action_policy(arguments, [])
        self.assertEqual(len(policy['routes']), 2); self.assertEqual(len(bindings), 2)
        self.assertIn('unverified_model', policy['required_acknowledgements'])
        self.assertFalse(policy['routes'][1]['generation_verified'])

    def test_retired_catalog_model_is_denied_even_with_cost_permission(self):
        catalog = load_provider_catalog()
        arguments = ImageJobAdmissionService().prepare(ImageRequest(prompt='山谷', model='kolors')).arguments()
        original = next(model for model in catalog.models if model.id == 'kolors')
        policy_source = next(model.policy for model in catalog.models if model.policy is not None)
        retired = replace(original, policy=replace(policy_source, lifecycle='retired'))
        changed = replace(catalog, models=tuple(retired if model.id == 'kolors' else model for model in catalog.models))
        policy, _ = action_policy(arguments, [], catalog=changed)
        self.assertFalse(policy['allowed']); self.assertIn('model_retired', policy['blocked_reasons'])


if __name__ == '__main__': unittest.main()
