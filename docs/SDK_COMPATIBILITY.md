# 从 v0.2.13 迁移旧 SDK

本文对应 GitHub 正式版 v0.2.13 升级到包含新版 Studio 和运行合同的后续版本。客户端可用自己的隔离环境逐项核对下列行为。

## 管理 Cookie 请求

登录响应和已认证的 `GET /v1/admin/session` 返回 `session_context`。管理请求以及通过 Cookie 访问的生成、下载接口，携带同一会话的 `X-Studio-Session-Context` 请求头。原生 EventSource 可在支持的管理流入口用 `studio_session_context` 查询参数。网关 API Key 继续用于网关接口，管理接口仍要求管理会话。

收到 `409 session_context_required` 时，先确认当前登录并取得会话标记。收到 `409 session_context_changed` 时，重新确认账户和待操作内容后再提交写入。不要把旧写入自动重放到新会话。[详细协议](admin-session-context.md)

## 视频任务状态

提交时保存原返回的 `task_id`，以及响应包含的 `job_id`。网关客户端可用 `GET /v1/videos/{task_id}` 查询有本地身份的任务。Studio 队列可通过管理端 `GET /v1/admin/jobs/{job_id}` 读取本地进度，原 worker 负责后续处理。

- `404 video_task_record_unavailable` 表示网关缺少匹配任务及缓存。它无法证明上游失败。需要回查时，先用原环境或原平台记录核对任务与账号归属，避免重复提交。
- 若返回 `record_only: true` 和 `origin_verified: false`，客户端可展示留存状态，把结果标记为本地记录，并仅在有 `video_url` 时提供作品入口。独立核验上游结果时仍需原始归属证据。
- `409 video_task_identity_ambiguous` 表示同一个外部任务号匹配多条视频任务。先核对本地任务和原渠道记录，再决定后续处理。

[视频升级前盘点](VIDEO_UPGRADE_PREFLIGHT.md)

## 图片模型选择

在 `POST /v1/media/route` 中省略 `requested_model` 可使用自动路由。指定模型时先查 `GET /v1/models`，管理端还可通过 `GET /v1/admin/catalog` 查可选型号和对应渠道。客户端须处理 `400 invalid_model_selector`，并让调用方选择已知别名或明确的 `provider::上游模型ID`，避免悄悄将显式模型请求改送其他渠道。[模型路由](SKILL_MEDIA_ROUTING.md)

## 助手请求与断线恢复

`POST /v1/assistant/chat` 和 `POST /v1/assistant/chat/stream` 接收 `message`、可选 `session_id`、可选 UUID `request_key` 和可选 `language`（`zh` 或 `en`）。将客户端自定义字段留在本地，服务端对未声明字段返回 `400 invalid_assistant_request`。

持久助手运行可用时，用同一 `request_key` 重试同一提交。已有运行断线时保留 `run_id`，使用 `GET /v1/admin/assistant/runs/{run_id}/stream` 和原 `Last-Event-ID` 接续读取。能读取旧会话但收到 `409 legacy_session_read_only` 时，先通过 `POST /v1/admin/assistant/conversations` 创建自己的会话，再用新会话号提交。[会话与流式鉴权](admin-session-context.md)

## 媒体下载与自动生成的 SDK

`/generated/{filename}`、`/uploads/{filename}` 和 `/v1/admin/media/{area}/{filename}` 各自提供 GET、HEAD。新版 OpenAPI 为这两种方法提供固定的独立操作名，后缀分别是 `__get` 和 `__head`。如果 SDK 从旧 OpenAPI 生成，请重新生成并确认下载调用实际发送 GET。现有 HTTP 路径和鉴权方式继续适用。

GET 的 200 和 206 响应请读取文件字节。HEAD 通过响应头提供元数据，响应体为空。分段下载仍用 `Range` 请求头，并读取响应的 `Content-Range`。

## SVG 和参考图

旧上传目录中的 SVG 和 SVGZ 返回 404。参考图如果来自已有 SVG，可先把可信文件转换为 PNG，再走正常上传。经过静态 SVG 检查的生成作品走产物目录下载。已发布的分层图片组继续通过作品和任务详情读取，撤下层组后其成员也不再可用。
