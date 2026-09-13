# fnOS Installation Diagnostics Runbook

fnOS 安装问题先区分应用包依赖、端口占用、Python 离线依赖、应用数据卷和启动失败。v0.2.13 新安装默认使用 Local Queue，当前包不再把 fnOS Redis 应用作为硬依赖；已有安装升级时保留原 queue backend。始终以当前包 manifest/App Center dependency list 为准，不要从旧版本经验推断当前依赖。

旧版本若曾声明 fnOS Redis 为硬依赖，而用户已有 Docker Redis 占用宿主机 6379，App Center 安装官方 Redis 可能发生端口冲突。v0.2.13 的受管 fnOS 包可在 Studio > 系统中检测已保存 Redis 或本机 `127.0.0.1:6379`，也允许管理员显式填写其他 Redis 映射地址；只有 Redis PING 成功且没有活跃 job/dispatch 时才允许在 Local 与 Redis/Celery 间切换。Assistant 只能读取安全诊断与日志，不得自行触发切换或重启。

升级时若 App Center 检测到 dependency app changes，应在 UI 审核依赖变化，不要用脚本绕过平台确认。安装失败时优先检查 App Center 错误、包依赖、目标卷和应用日志，不要删除已有数据目录来尝试修复。
