# Current State

日期：2026-06-05

## 项目

AngeMedia Gateway v0.2.0。

当前主线目标：轻量、自托管、Agent 友好、Web 可配置的图片/视频生成任务网关。

当前阶段：Phase 1 后端结构拆分，不改行为。

当前原则：低风险阶段可以适当合并，不必过度细拆；高风险任务仍必须细拆。

## 最近关键提交

- `7d6505b docs: add agent handoff logs`
- `f760e47 refactor: move provider list read to admin service`
- `8d101b3 refactor: move assistant admin logic to service`
- `a3499ed test: cover assistant admin endpoints`

## 当前硬约束

- Apache License 2.0。
- v0.2 不做多用户注册。
- 多个 Gateway API Key 可以支持，但不是多用户系统。
- DB 是运行时主配置，env 只做启动配置。
- v0.2 不做 v0.1 数据迁移兼容。
- 默认中文 UI，预留 i18n 架子。
- Docker / Compose 是主线部署。
- fnOS 只预留 `packaging/fnos/`。
- README / SKILL / 发布文档后置。
- LLM Copilot 只属于 Web Studio / Assistant API，不进入核心生成链路。
- 中文用户输入，模型侧优先英文 prompt。
- 本地资产库是核心卖点。
- Fake / Mock Provider 后续必须做。
- 安全回归清单必须集中覆盖 SSRF、路径穿越、密钥脱敏、下载大小限制。
- 不引入 Redis / Celery / Kubernetes。
- 不引入 Vue / React。
- 前端使用 HTML + CSS + vanilla JS modules。

## 当前后端结构拆分状态

- `routes/media.py` 已抽离媒体生成编排到 `services/media_service.py`。
- `routes/admin.py` 已抽离大部分 read/write/provider/assistant admin 编排到 `services/admin_service.py`。
- `routes/admin.py` 仍保留 FastAPI route、认证依赖、Cookie/Header/Request/Response 处理、HTTPException 映射、login/logout/session/password 等高风险认证路径。

## 当前测试基线

最近一次已验收测试：

```powershell
pytest tests/test_admin_api.py
```

结果：`21 passed`。
