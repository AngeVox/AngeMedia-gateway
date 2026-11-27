# Agnes 视频模型调用示例

> AngeMedia v0.2.15 可显式选择 Agnes Video 2.5 和 2.5 Flash。API 保留 v2.0 作为兼容默认值。使用前请核对渠道账号的模型权限。返回的 `video_id` 由网关管理。

## 一、入口

```text
POST /v1/videos
GET  /v1/videos/{task_id}   # 仅用于 path-safe 兼容 task ID 的人工查询
```

网关优先使用 `/agnesapi?video_id=...` 查询视频。2.5 系列查询会附加所选型号作为 `model_name`。

## 二、Video 2.5 文生视频

```json
{
  "model": "agnes-video-2.5",
  "prompt": "一只橘猫戴着墨镜走过霓虹灯街道，电影感镜头，雨夜反光，缓慢推进。",
  "mode": "text",
  "seconds": "5",
  "size": "720P",
  "aspect_ratio": "16:9",
  "wait_for_completion": false
}
```

当前参数：

- `seconds`: 字符串 `"4"` ～ `"12"`
- `size`: `720P` / `1080P` / `1K` / `2K`
- `aspect_ratio`: `21:9` / `16:9` / `4:3` / `1:1` / `3:4` / `9:16`
- `mode`: `text` / `keyframe` / `reference`
- `seed`: 可选整数

不要把 v2.0 的 `width`、`height`、`num_frames`、`frame_rate`、`num_inference_steps` 混入 2.5 请求。

## 三、Video 2.5 参考图模式

```json
{
  "model": "agnes-video-2.5",
  "prompt": "保持人物身份和场景风格，加入自然动作和轻微镜头推进。",
  "mode": "reference",
  "images": [
    "https://example.com/reference-1.jpg",
    "https://example.com/reference-2.jpg"
  ],
  "seconds": "5",
  "size": "720P",
  "aspect_ratio": "16:9"
}
```

标准版 2.5 最多发送 8 张参考图。Agnes 上游最终接收公开 `http(s)` 图片 URL：安全公网 URL 可直接使用；网关自有 `/uploads/*`、`/generated/*` 或安全 data URL 在配置 Reference Relay 后会自动发布为短时公网 URL。未配置 Relay 时，本地引用会在提交前明确拒绝。

## 四、Video 2.5 关键帧模式

```json
{
  "model": "agnes-video-2.5",
  "prompt": "从首帧平滑过渡到尾帧，镜头自然推进，光影连续。",
  "mode": "keyframe",
  "first_frame": "https://example.com/first.jpg",
  "last_frame": "https://example.com/last.jpg",
  "seconds": "6",
  "size": "1080P",
  "aspect_ratio": "16:9"
}
```

`first_frame` / `last_frame` 至少提供一个。它们可以是安全公网 URL；配置 Reference Relay 后，也可以使用网关自有本地图片引用，由 AngeMedia 在提交前转换为公开 URL。

## 五、v2.0 兼容请求

若账号仍可使用 Agnes Video v2.0，可以直接引用网关自有的 `/uploads/*` 或 `/generated/*` 图片资产。该路径无需配置 Reference Relay。

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "让人物缓慢转头，背景光影轻微移动。",
  "image": "/uploads/reference.png",
  "width": 1152,
  "height": 768,
  "num_frames": 121,
  "frame_rate": 24
}
```

v2.0 常用帧数：`81`、`121`、`161`、`241`、`441`。这套帧数式合同不用于 2.5。

## 六、异步结果

提交后优先使用返回的 `job_id` 在 Web Studio Jobs / Assets 查看。Agnes 完成响应里的 `metadata.url` 会被归一化并尝试安全本地化为 `/generated/*` 资产。

如果渠道明确返回 `video_queue_full` 且未提供任务号，Studio 会提示本次未受理。缺少明确业务码的 503 且未取到任务号时，请先核对渠道任务和扣费记录。网关在这两种情况下都不会自动重提。

## 七、Video 2.5 Flash

使用上方文生视频示例时，将 `model` 设为 `agnes-video-2.5-flash`，`size` 使用 `720P`。Flash 支持 4–12 秒、最多 5 张参考图和 3 条参考音频。查看 [2.5 系列参考素材](SKILL_VIDEO_GENERATION.md#25-系列参考素材) 了解两种型号的具体字段。
