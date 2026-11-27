"""渠道目录运行字段的安全投影断言，供管理和选型测试共用。"""
from datetime import datetime


def assert_provider_runtime_projection(testcase, provider):
    runtime = provider['runtime']
    expected = {'enabled', 'api_key_configured'}
    has_directory = 'model_directory' in runtime and provider['id'] == 'modelscope'
    if has_directory:
        expected.add('model_directory')
    testcase.assertEqual(set(runtime), expected, provider['id'])
    for key in ('enabled', 'api_key_configured'):
        testcase.assertIs(type(runtime[key]), bool, key)

    if has_directory:
        directory = runtime['model_directory']
        required = {'status', 'evidence_state', 'server_time'}
        optional = {'expires_at', 'model_list_validated', 'project_model_visibility'}
        testcase.assertLessEqual(required, set(directory))
        testcase.assertLessEqual(set(directory), required | optional)
        testcase.assertIsNotNone(datetime.fromisoformat(directory['server_time']).tzinfo)
        if 'model_list_validated' in directory:
            testcase.assertIs(type(directory['model_list_validated']), bool)
        if 'project_model_visibility' in directory:
            testcase.assertEqual(directory['evidence_state'], 'current')
            testcase.assertIs(directory['model_list_validated'], True)
            visibility = directory['project_model_visibility']
            testcase.assertEqual(set(visibility), {'listed', 'not_listed'})
            for items in visibility.values():
                testcase.assertIsInstance(items, list)
                testcase.assertTrue(all(isinstance(item, str) for item in items))
