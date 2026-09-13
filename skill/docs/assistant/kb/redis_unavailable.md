# Redis Unavailable Runbook

只有当前 queue backend 实际使用 Redis/Celery 时，Redis unavailable 才会阻断 broker delivery。先读取 Diagnostics 的 queue backend 和 healthy 状态，再判断 Redis 是否相关；不要因为看到 redis 字样就假定所有部署必须安装 Redis。

Redis/Celery 模式下，常见现象是 dispatcher 发布失败、dispatch last_error 增加、queued 任务没有新的 worker attempt。检查已配置 Redis 服务是否可达、端口是否与其他服务冲突，以及当前配置是否指向预期实例。不要在 Assistant 中显示 broker URL、密码或连接字符串。

Redis 不是 Job 真相源。任务状态与 outbox 在 SQLite 中持久化，因此 broker 故障恢复后应继续基于现有 Job/dispatch 状态处理，而不是删除任务重新提交。