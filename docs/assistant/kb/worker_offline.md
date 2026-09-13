# Worker Offline Runbook

当 Diagnostics 显示 queue backend 可用，但任务没有 worker attempt、active jobs 持续积压时，考虑 worker/dispatcher 进程状态异常。先使用安全 Diagnostics 和 queue_status 判断 backend、healthy、dispatch errors 和状态计数，不要让 Assistant 直接重启服务。

Celery 部署需要独立 worker 消费 broker 消息；其他 queue backend 可能采用不同执行方式，因此不要假定所有部署都必须存在 Celery worker。以 Diagnostics 报告的当前 backend 和进程模型为准。

如果 worker 恢复后存在旧消息，依赖 Job/attempt 的幂等和恢复规则处理，不要手工复制原始消息或重放 Provider payload。