# Agnes 模型调用索引

> 本页是 AngeMedia v0.2.13 的 Agnes 当前调用索引。具体字段以 adapter、catalog 和测试为准；没有实际接入的上游能力不写成可用合同。

## 当前模型

| 类型 | 当前推荐 | 兼容模型 | 文档 |
|---|---|---|---|
| 图片 | `agnes-image` / `agnes-2.5` → `agnes-image-2.5-flash` | `agnes-2.1`、`agnes-2.0` | `docs/AGNES_IMAGE_CALL_EXAMPLES.md` |
| 视频 | `agnes-video-v2.0`（稳定默认） | `agnes-video-2.5`（显式、可能需权限） | `docs/AGNES_VIDEO_CALL_EXAMPLES.md` |

## v0.2.13 已验证适配

- Agnes Image 2.5 Flash：文生图、最多 4 张参考图、命名尺寸档位/比例，并保留 2.1/2.0 兼容。
- Agnes Video 2.5：`text` / `keyframe` / `reference`，4–12 秒，720P/1080P/1K/2K。
- 视频当前轮询：优先 `/agnesapi?video_id=...`；2.5 自动附加 `model_name=agnes-video-2.5`。
- `video_id` 作为 opaque external ID 持久化；只有真正的 URL path 兼容接口继续使用 strict path-safe `task_id` 校验。
- 完成视频优先读取 `metadata.url`，再进入安全本地化和 Assets。

## 安全边界

- Image 参考图继续走现有 data URL / gateway asset materialization 与大小限制。
- Video 2.5 上游最终需要公开安全图片 URL；安全公网 URL 可直接使用，网关自有 `/uploads/*`、`/generated/*` 或安全 data URL 在配置 Reference Relay 后会自动发布为短时公网 URL。官方存在的 audio/video reference 尚未进入 v0.2.13 发布合同。
- 不自动透传未验证字段。
