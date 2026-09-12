# Provider 5xx Runbook

Provider 5xx 通常是上游临时故障、网关异常或服务过载。先确认渠道已配置且认证正常，再比较同一渠道不同模型或其他渠道是否同时失败。Job 中的 error_code、error_category、retryable 和 gateway_stage 比 Provider 原始 body 更适合作为排障依据。

对于明确发生在查询/poll 阶段的 5xx，可以按现有有界重试策略继续查询；对于 submit 阶段出现连接中断或不明确的 5xx，需要警惕 Provider 可能已经接受任务，不能盲目重新 submit。

持续 5xx 时应检查 Provider 官方状态和渠道连接；Assistant P1 不直接联网，也不执行自动切换或重试。