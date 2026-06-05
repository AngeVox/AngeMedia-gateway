# Next Task

日期：2026-06-05

## 当前推荐下一步

**Phase 2.1-C 已完成**，下一步进入 **Phase 2.1-D：Mock Provider media API 端到端测试**。

每轮必须更新：

- `docs/dev-handoff/PHASE_LOG.md`
- `docs/dev-handoff/NEXT_TASK.md`

不要自动开始下一步代码修改，必须等待用户明确批准。

## Phase 2.1-C 完成结论

- ✅ 已完成 Mock Provider routing 接入（`routing.py` 新增 mock alias 和单 provider 返回判断）
- ✅ 已完成 Mock Provider registry 注册（`providers/image.py` 新增 MockImageProvider）
- ✅ `resolve_chain("mock")` 只返回 `[RouteTarget("mock", "mock-model")]`，无 pollinations fallback
- ✅ 测试覆盖充足（6 + 6 + 21 = 33 个用例通过）
- ❌ 未接入 Admin API / media API
- ❌ 未修改 state.py（mock provider 默认 enabled）

## 下一位 Agent 接手前必须做

1. 读取 `docs/dev-handoff/*`。
2. 执行并确认：

```powershell
git status --short --branch
```

3. 确认当前用户批准的 Phase 和允许修改文件。
4. 明确列出不做事项。
5. 高风险任务先输出计划，等待确认后再修改。

## 当前禁止事项

- 不允许自动进入下一阶段。
- 不允许开始 LLM Copilot。
- 不允许开始统一 LLM Client。
- 不允许开始重试策略。
- 不允许启动 Job / DB / Queue。
- 不允许启动 Web Studio 重构。
- 不允许修改 login/logout/session/password/cookie。
- 不允许修改 README、SKILL、发布文档。
- 不允许引入 Redis / Celery / Kubernetes。
- 不允许引入 Vue / React。
- 不允许改 schema 或权限模型，除非后续 Phase 明确批准。

## 交接备注

- Codex 更适合高风险/复杂任务。
- CC + mimo 更适合低中风险小任务。
- 当前测试保护重点在 `tests/test_admin_api.py`。
- Phase 1.2B 已收尾，不应继续拆分认证层。

## 最近关键提交

- `91420f1 docs: sync agent handoff state`
- `7d6505b docs: add agent handoff logs`
- `f760e47 refactor: move provider list read to admin service`
- `8d101b3 refactor: move assistant admin logic to service`
- `a3499ed test: cover assistant admin endpoints`
