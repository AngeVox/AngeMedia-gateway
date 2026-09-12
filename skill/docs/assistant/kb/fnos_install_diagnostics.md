# fnOS Installation Diagnostics Runbook

fnOS 安装问题先区分应用包依赖、端口占用、Python 离线依赖、应用数据卷和启动失败。不同 AngeMedia 包版本可能声明不同 App Center 依赖，因此以当前包 manifest/App Center dependency list 为准，不要假定 Redis 永远是必需项。

如果某版本声明 fnOS Redis 为硬依赖，而用户已经运行 Docker Redis 占用宿主机 6379，App Center 安装官方 Redis 可能发生端口冲突。用户可以调整自己的 Docker 映射，但 AngeMedia 的长期设计不应要求用户为应用让出固定 Redis 端口；更合适的是把 queue backend 当作部署能力而不是业务真相源。

升级时若 App Center 检测到 dependency app changes，应在 UI 审核依赖变化，不要用脚本绕过平台确认。安装失败时优先检查 App Center 错误、包依赖、目标卷和应用日志，不要删除已有数据目录来尝试修复。