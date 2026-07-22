# 视频适配拆分设计

> Archived / historical design reference, not current v0.2.11 release contract.

## 为什么要拆出来

图片生成是这个网关的主线。视频生成参数多、耗时长、异步轮询复杂，如果直接堆进应用装配入口，主流程会越来越臃肿，也会影响 Agent 读取 Skill 时的清晰度。

所以 v0.1.0 开始采用以下边界：

- `scripts/proxy.py`：只作为兼容启动入口。
- `server.py`：只负责 FastAPI 应用装配。
- `routes/media.py`：保留图片/视频 HTTP 路由入口。
- `adapters/agnes_video.py`：只负责 Agnes 视频请求结构、payload 构造、提交任务、轮询任务和返回标准化。
- `docs/AGNES_MODEL_CALL_EXAMPLES.md`：放详细调用示例。
- `SKILL.md`：只做简短索引，不展开视频细节。

## 后续新增视频渠道的规则

新增视频渠道时，不要直接把代码写进 `server.py` 或路由函数。推荐创建：

```text
adapters/<provider>_video.py
```

每个视频适配器至少包含：

1. 请求模型结构。
2. `build_payload()`。
3. `submit_task()`。
4. `poll_task()`。
5. 可选的 `generate_video()` 同步等待方法。
6. 统一返回字段：`task_id`、`status`、`video_url`、`progress`。

## 为什么不把视频写入 SKILL.md

视频的调用方式和宿主平台的文件发送方式变化很快。Skill 的核心任务是让 Agent 快速判断意图、生成图片、增强提示词。如果把视频长文档也写进去，会导致每次普通生图都白读一堆视频内容。

所以 SKILL.md 只保留：

```text
需要视频时，查看 docs/AGNES_MODEL_CALL_EXAMPLES.md。
```

这比把视频细节塞进 Skill 更稳。
