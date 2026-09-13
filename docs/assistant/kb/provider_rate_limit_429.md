# Provider 429 / Rate Limit Runbook

HTTP 429、rate limit、quota 或 insufficient balance 通常归类为 provider_rate_limited。先确认是否只有单个渠道/模型受影响，再看 retryable 和 human_hint。短期限速可以等待后重试；额度或余额不足则需要在 Provider 侧处理。

不要通过高频自动重试绕过 Provider 限速。队列恢复应保持有界 backoff，并避免同一生成请求并发重复提交。若多个用户同时触发 429，可降低并发、延长重试间隔或改用其他已配置且具备对应能力的渠道。

Assistant 只能给出诊断和下一步检查，不应自行修改并发、额度或渠道开关。