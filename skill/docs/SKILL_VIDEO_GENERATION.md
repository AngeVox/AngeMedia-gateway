# 视频生成子技能

> 本文档处理视频任务的意图判断、输入模式判断、提示词增强和网关调用。v0.2.12 当前默认视频模型是 Agnes Video 2.5；Agnes Video v2.0 保留兼容。

## 一、视频任务工作流

1. 判断是不是视频任务。
2. 判断输入模式：`text` / `keyframe` / `reference`。
3. 需要参考图时判断它是首帧、尾帧还是普通 reference。
4. 整理 prompt，补主体动作、环境、光影、镜头运动和节奏。
5. 组装 `POST /v1/videos`，默认 `wait_for_completion: false`。
6. 提交后返回 `job_id` / `task_id`，让用户在 Web Studio Jobs / Assets 查看结果；Agent 不主动持续轮询。

v0.2.12 的队列 worker 负责 Agnes submit / poll / asset import。Provider 返回的 `video_id` 是 opaque external ID；Agent 不应自行拼接 Provider URL。

## 二、当前 Agnes Video 2.5 合同

模型：`agnes-video-2.5`（兼容别名 `agnes-video-v2.5`）。

| 参数 | 当前约束 |
|---|---|
| `mode` | `text` / `keyframe` / `reference` |
| `seconds` | 字符串 `"4"` ～ `"12"`，默认 `"5"` |
| `size` | `720P` / `1080P` / `1K` / `2K` |
| `aspect_ratio` | `21:9` / `16:9` / `4:3` / `1:1` / `3:4` / `9:16` |
| `seed` | 可选整数 |
| `first_frame` / `last_frame` | keyframe 模式公网图片 URL；至少一个 |
| `images` | reference 模式公网图片 URL 数组，网关当前最多 8 张 |

2.5 明确不使用 v2.0 的 `width`、`height`、`num_frames`、`frame_rate`、`num_inference_steps`。不要混发两套参数。

### 公网参考图边界

Agnes Video 2.5 需要上游自己抓取参考媒体，因此 `first_frame`、`last_frame`、`images` 必须是可公开访问的 `http(s)` URL。AngeMedia 会做 SSRF/地址安全校验，但不会把受保护的 `/uploads/*` 或 `/generated/*` 私有资产暴露成裸链。

如果只有 AngeMedia 本地受保护资产，可以显式选择兼容模型 `agnes-video-v2.0`，沿用网关安全物化路径。

## 三、推荐请求

### A. 文生视频

```json
{
  "model": "agnes-video-2.5",
  "prompt": "A cinematic aerial shot of a futuristic city at night, neon reflections on wet roads, slow camera movement, light fog.",
  "mode": "text",
  "seconds": "5",
  "size": "720P",
  "aspect_ratio": "16:9",
  "wait_for_completion": false
}
```

### B. 参考图视频

```json
{
  "model": "agnes-video-2.5",
  "prompt": "Keep the character identity and scene style, add subtle natural motion and a gentle camera push-in.",
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

### C. 首尾关键帧

```json
{
  "model": "agnes-video-2.5",
  "prompt": "A smooth cinematic transition from the opening frame to the ending frame with coherent lighting and motion.",
  "mode": "keyframe",
  "first_frame": "https://example.com/first.jpg",
  "last_frame": "https://example.com/last.jpg",
  "seconds": "6",
  "size": "1080P",
  "aspect_ratio": "16:9"
}
```

## 四、Agnes Video v2.0 兼容合同

仅在需要旧帧数合同或需要把 AngeMedia 受保护图片资产安全物化给上游时显式使用 `agnes-video-v2.0`。

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "A cinematic shot with smooth natural motion.",
  "width": 1152,
  "height": 768,
  "num_frames": 121,
  "frame_rate": 24,
  "image": "/uploads/reference.png"
}
```

v2.0 常用 `num_frames`：`81`、`121`、`161`、`241`、`441`；`frame_rate` 范围 `1–60`。这套字段不要用于 Video 2.5。

## 五、提示词增强

视频 prompt 应至少覆盖：主体、动作、环境、光影、镜头运动、节奏和时间连续性。用户已经明确的限制必须保留，不擅自改变人物身份、构图或关键剧情。

## 六、异步策略

默认只提交：

```text
POST /v1/videos
```

Agent 返回 `job_id` / `task_id` 后，让用户到 Web Studio Jobs / Assets 查看。`GET /v1/videos/{task_id}` 保留给 path-safe 兼容 task ID 的人工排查；当前 Provider 的 opaque `video_id` 由网关 job/worker 内部管理，不要求 Agent 拼接查询路径。

`wait_for_completion=true` 只在宿主明确能承受长连接时使用，不作为 Agent 默认。

## 七、本地化与失败处理

完成后网关会安全下载 Provider 返回的远程视频并写入 Assets，成功时优先使用本地 `/generated/*` 地址。

- 提交 HTTP 503 且没有任务 ID：不自动重提，避免重复生成/扣费。
- reference/keyframe URL 无法通过公网安全校验：要求换公开安全 URL，或改用 v2.0 + 网关资产。
- 已拿到 `job_id`：不要自行做高频 Provider 轮询。
