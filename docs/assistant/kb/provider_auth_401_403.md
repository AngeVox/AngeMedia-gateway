# Provider 401 / 403 Runbook

Provider 返回 401/403 通常属于 auth_failed，但 403 也可能表示账号没有目标模型权限。先看 channel_safe_summary 的 enabled、key_configured 和 default_model，再看 Job 的 error_category/human_hint。不要在排障回复中显示 API Key、认证头或 Provider 原始响应。

如果同一个 key 对旧模型可用、对新模型稳定 403，应优先考虑模型权限或账号区域边界，而不是自动切换域名、重复提交或改写请求。若所有模型都 401/403，再检查 key 是否配置到正确渠道和服务区域。

连接测试只能用于验证已配置渠道，不能把任意用户 URL 当作探测目标。