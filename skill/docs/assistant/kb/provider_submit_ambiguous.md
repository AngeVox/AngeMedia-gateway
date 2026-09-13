# Ambiguous Provider Submit Runbook

ambiguous_submit 表示提交生成请求后，AngeMedia 无法安全确认 Provider 是否已接受任务或没有拿到可验证的 task id。此时自动重提可能产生重复生成、重复计费或两个无法对应的上游任务，因此默认应保守失败而不是无限重试。

如果 Job 已经记录 external task id，则后续恢复应围绕已有任务进行 poll/query，不应再次 submit。如果没有 task id，只能根据 Provider 控制台、官方任务记录或人工确认判断是否需要重新发起。

不要为了“解卡”复制原始 payload、清空 Job 状态或绕过幂等保护。