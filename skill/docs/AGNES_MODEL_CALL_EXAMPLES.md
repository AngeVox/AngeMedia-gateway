# Agnes 模型调用索引

> 本页列出 AngeMedia 已适配的 Agnes 型号。模型可用性和费用以渠道账号及官方政策为准。

## 当前模型

| 类型 | 可选模型 | 兼容模型 | 文档 |
|---|---|---|---|
| 图片 | `agnes-image` / `agnes-2.5` → `agnes-image-2.5-flash` | `agnes-2.1`、`agnes-2.0` | [图片调用示例](AGNES_IMAGE_CALL_EXAMPLES.md) |
| 视频 | `agnes-video-2.5`、`agnes-video-2.5-flash` | `agnes-video-v2.0`（API 兼容默认） | [视频调用示例](AGNES_VIDEO_CALL_EXAMPLES.md) |

## 模型能力

- Agnes Image 2.5 Flash：文生图、最多 4 张参考图、命名尺寸档位/比例，并保留 2.1/2.0 兼容。
- Agnes Video 2.5 和 2.5 Flash 支持 `text`、`keyframe`、`reference` 和 4–12 秒。标准版支持 720P、1080P、1K、2K，Flash 支持 720P。
- 网关优先使用 `/agnesapi?video_id=...` 查询视频，2.5 系列会附加所选型号作为 `model_name`。
- `video_id` 作为 opaque external ID 持久化；只有真正的 URL path 兼容接口继续使用 strict path-safe `task_id` 校验。
- 完成视频优先读取 `metadata.url`，再进入安全本地化和 Assets。

## 安全边界

- Image 参考图继续走现有 data URL / gateway asset materialization 与大小限制。
- 2.5 系列可直接使用通过校验的公开图片链接。配置 Reference Relay 后，网关可将自己的上传、生成资产和受支持的 data URL 转为短时有效的公开链接。
- 不自动透传未验证字段。

- 音频和视频参考使用公开链接。各型号接受的输入及数量见 [2.5 系列参考素材](SKILL_VIDEO_GENERATION.md#25-系列参考素材)。
