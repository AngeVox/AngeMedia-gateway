"""助手任务卡的最小观察值；只投影已知阶段和错误，不公开上游正文或外部任务凭据。"""

_STAGES = frozenset({'image_generate', 'video_submit', 'video_poll', 'asset_import', 'finalize',
    'queued', 'provider_request', 'provider_response', 'provider_poll', 'download'})
_ERRORS = frozenset({'ambiguous_submit', 'video_submit_ambiguous', 'video_submit_queue_full', 'video_submit_dispatch_pending',
    'video_poll_timeout', 'video_pipeline_timeout', 'video_poll_failed', 'video_result_not_ready',
    'video_generation_failed', 'video_asset_import_failed', 'video_result_missing_url', 'video_asset_url_rejected',
    'asset_import_failed', 'download_failed', 'provider_timeout', 'timeout',
    'action_execution_connection_changed', 'action_execution_policy_denied', 'action_execution_reference_changed',
    'action_execution_conditions_changed', 'action_execution_receipt_changed', 'action_execution_job_changed',
    'action_execution_target_changed', 'action_execution_validation_failed', 'video_provider_disabled',
    'video_origin_unverified', 'video_origin_job_changed', 'video_origin_task_changed',
    'video_origin_connection_changed', 'video_origin_submit_already_attempted', 'video_origin_reference_relay_unavailable',
    'video_wait_interrupted', 'video_submit_persistence_pending', 'video_result_persistence_pending', 'video_execution_preparation_failed'})
_CATEGORIES = frozenset({'auth_failed', 'model_unavailable', 'content_filtered', 'provider_rate_limited',
    'local_budget', 'action_authorization', 'provider_protocol', 'unsafe_remote_url', 'ambiguous_submit', 'provider_identity'})
_STAGES = _STAGES | {'image_poll','image_import'}
_ERRORS = _ERRORS | {'image_submit_ambiguous','image_submit_rejected','image_task_dispatch_pending',
    'image_poll_timeout','image_import_timeout','image_generation_failed','image_query_retry','image_query_failed',
    'image_import_retry','image_task_result_missing','image_task_origin_unverified','image_task_connection_changed',
    'image_task_identity_changed','image_task_job_changed','image_task_receipt_changed',
    'image_layer_submit_unknown','image_layer_result_invalid','image_layer_receipt_missing',
    'image_layer_links_expired','image_layer_artifact_invalid','image_layer_import_failed','image_layer_import_retry'}
_ERRORS = _ERRORS | {'pollinations_video_submit_unknown', 'pollinations_video_auth', 'pollinations_video_balance',
    'pollinations_video_permission', 'pollinations_video_rate_limit', 'pollinations_video_request_rejected',
    'pollinations_video_reference_unavailable', 'pollinations_video_read_retry', 'pollinations_video_read_failed',
    'pollinations_video_result_unavailable', 'pollinations_video_timeout', 'pollinations_video_job_changed',
    'pollinations_video_connection_changed', 'pollinations_video_receipt_invalid'}
_CATEGORIES = _CATEGORIES | {'local_timeout', 'task_failed', 'provider_response', 'billing', 'permission',
    'auth', 'rate_limit', 'network', 'validation', 'queue_full'}


def assistant_job_observation(job):
    def known(value, choices):
        return value if isinstance(value, str) and value in choices else 'unknown' if value else None
    return {'id': job['id'], 'status': known(job.get('status'), {'queued', 'running', 'succeeded', 'failed', 'canceled'}),
            'kind': known(job.get('kind'), {'image', 'video'}),
            'stage': known(job.get('stage'), _STAGES),
            'error_code': known(job.get('error_code'), _ERRORS),
            'error_category': known(job.get('error_category'), _CATEGORIES),
            'external_task_present': bool(job.get('external_task_id'))}
