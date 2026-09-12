# Agnes Video Opaque ID Runbook

Agnes Video 的上游 video_id 是 opaque external id，不应按本地文件名或 URL path segment 规则限制长度/字符。AngeMedia 对外部 task id 使用独立校验，并通过查询参数进行当前异步状态查询；不要把 Provider id 拼进本地文件路径。

排障时如果 submit 已成功拿到 video_id，但后续 poll 失败，应保留已有 external task id 并恢复查询，避免重新 submit。v2.0 是当前稳定默认；Video 2.5 可选择，但账号可能需要额外模型权限。

Assistant 只需要报告“external task id 已存在/不存在”和安全状态，不应回显超长 opaque id 本身，也不应展示 Provider 原始响应。