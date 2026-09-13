# Job Timeout Runbook

任务 timeout 或 video_poll_timeout 表示任务在允许的等待窗口内没有完成，不等于 Provider 一定失败。先看 Jobs 中的 status、stage、provider_status、attempt_count、retryable 和 human_hint，再看 Diagnostics 的 queue/database/provider 摘要。不要仅凭前端等待时间判断任务已经丢失。

如果任务仍是 running 且处于 video_poll，优先判断 Provider 是否仍在处理以及轮询窗口是否合理；视频通常明显慢于图片。ModelScope 等异步服务也可能长时间保持 RUNNING。若任务已经 failed，依据 error_category 和 human_hint 决定是调整超时、检查渠道，还是等待 Provider 恢复。

不要因为 timeout 自动重新提交可能计费的生成请求。对于已获得上游 task id 的视频任务，应优先恢复查询已有任务，而不是重新 submit。