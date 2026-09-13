# Agnes 视频模型调用示例

> AngeMedia v0.2.13 默认使用已实测稳定的 Agnes Video v2.0；Video 2.5 保留为显式可选模型，并可能需要模型级账号权限。Provider 返回的 `video_id` 一律视为 opaque external ID，由 job/worker 内部管理。

## 一、入口

```text
POST /v1/videos
GET  /v1/videos/{task_id}   # 仅用于 path-safe 兼容 task ID 的人工查询
```

异步队列执行器会优先通过 Agnes 推荐接口 `/agnesapi?video_id=...` 轮询。Local Queue 在 dispatcher 进程内执行任务，Redis/Celery 模式由 worker 执行；2.5 自动附加 `model_name=agnes-video-2.5`。

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

v0.2.13 网关当前最多发送 8 张参考图。Agnes 上游最终接收公开 `http(s)` 图片 URL：安全公网 URL 可直接使用；网关自有 `/uploads/*`、`/generated/*` 或安全 data URL 在配置 Reference Relay 后会自动发布为短时公网 URL。未配置 Relay 时，本地引用会在提交前明确拒绝。

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

## 五、v2.0 稳定默认合同与本地资产兼容

Agnes Video v2.0 继续保留直接物化网关自有 `/uploads/*` 或 `/generated/*` 图片资产的路径，不依赖 Reference Relay。需要在未配置 Relay 的环境中使用本地参考图时，可以显式选择旧模型：

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
