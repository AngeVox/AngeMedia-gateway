# AngeMedia Studio 设计说明

> Archived / historical design reference. Do not treat this file as the complete v0.2.1 release contract.

AngeMedia Studio 最早是 v0.1.0 内置的轻量 Web UI。v0.2.x 的实现已经换成更安全的 Web Studio，v0.2.1 进一步接入队列、任务中心、诊断和助手：单管理员登录、账号信息查看与 username/password 修改、catalog-aware Generate Image / Generate Video、Jobs/Assets 最小列表、自定义 Provider 管理，以及 builtin/catalog/reserved Provider 的只读 compact 展示。

当前 v0.2.1 明确边界：

- 修改 username/password 都需要 `current_password`，成功后会清除 session，需要重新登录。
- Gateway API Key 不能访问 admin account API。
- Generate Image 支持 default route、catalog model、自定义 provider；自定义 provider 的 `provider_model` 是上游模型 override。
- 图片生成成功后会本地化并写入 Assets；`/generated/*` 与 `/uploads/*` 需要认证，支持 authenticated `HEAD`。
- 视频支持提交与状态查询；没有独立后台进程持续检查视频完成状态。
- Jobs/Assets 是最小可用列表，不是完整诊断或事件分析界面。
- 不提供旧版数据自动导入/backfill，也不包含 Redis/Celery/K8s、多租户、计费或 SaaS 产品能力。

## 设计目标

- 能快速测试图片、视频、路由、提示词增强；
- 能查看 provider 状态；
- 能预览本地化后的 `/generated/` 结果；
- 风格上与 AngeVoice 属于同一系列，但不照搬。

## 与 AngeVoice 的关系

借鉴点：

- Ange 系列字标；
- 柔和渐变背景；
- 玻璃拟态面板；
- 顶部状态栏；
- 状态胶囊；
- light/dark 主题；
- Toast 提示。

差异点：

- AngeMedia 是媒体生成工具，结果预览区域更大；
- 工作台以图片/视频输出为中心；
- 保留路由建议和提示词增强按钮；
- 增加渠道状态和接入说明页签。

## 后续建议

v0.1.0 的前端只是轻量 Studio。后续可以继续补：

- 生成历史数据库；
- 视频任务队列；
- Provider 配置管理；
- Prompt 增强前后对比；
- 多图上传和角色标注；
- 更精致的 Ange 系列视觉系统。
