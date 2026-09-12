# Agnes 视频模型调用示例

> AngeMedia v0.2.12 默认使用 Agnes Video 2.5；v2.0 只保留兼容。Provider 返回的 `video_id` 被视为 opaque external ID，由 job/worker 内部管理。

## 一、入口

```text
POST /v1/videos
GET  /v1/videos/{task_id}   # 仅用于 path-safe 兼容 task ID 的人工查询
```

当前异步 worker 会优先通过 Agnes 推荐接口 `/agnesapi?video_id=...` 轮询。2.5 自动附加 `model_name=agnes-video-2.5`。

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

v0.2.12 网关当前最多发送 8 张参考图。`images` 必须是 Agnes 上游可直接访问的公开 `http(s)` 图片 URL，并通过 SSRF 地址校验。

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

`first_frame` / `last_frame` 至少提供一个，同样必须是公开安全 URL。

## 五、v2.0 本地资产兼容

只有 AngeMedia 受保护 `/uploads/*` 或 `/generated/*` 图片资产、又需要参考图视频时，可以显式使用旧模型：

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

HTTP 503 且未拿到任务 ID 时，网关不会自动重提，避免上游实际已接受请求时产生重复任务或重复费用。
