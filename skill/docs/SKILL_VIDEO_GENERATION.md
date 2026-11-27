# 视频生成子技能

> 本文档介绍视频任务的输入模式、提示词和调用方式。选用 Agnes Video 2.5 或 2.5 Flash 时请显式指定型号，并核对账号权限。API 保留 Agnes Video v2.0 作为兼容默认值。

## 一、视频任务工作流

1. 判断是不是视频任务。
2. 判断输入模式：`text` / `keyframe` / `reference`。
3. 需要参考图时判断它是首帧、尾帧还是普通 reference。
4. 整理 prompt，补主体动作、环境、光影、镜头运动和节奏。
5. 组装 `POST /v1/videos`，默认 `wait_for_completion: false`。
6. 提交后返回 `job_id` / `task_id`，让用户在 Web Studio Jobs / Assets 查看结果；Agent 不主动持续轮询。

队列负责提交任务、查询进度和保存作品。网关会跟踪渠道返回的 `video_id`，Agent 可以用 `job_id` 在 Studio 查看进度。

## 二、可选 Agnes Video 2.5 合同

模型：`agnes-video-2.5`（兼容别名 `agnes-video-v2.5`）。

| 参数 | 当前约束 |
|---|---|
| `mode` | `text` / `keyframe` / `reference` |
| `seconds` | 字符串 `"4"` ～ `"12"`，默认 `"5"` |
| `size` | `720P` / `1080P` / `1K` / `2K` |
| `aspect_ratio` | `21:9` / `16:9` / `4:3` / `1:1` / `3:4` / `9:16` |
| `seed` | 可选整数 |
| `first_frame` / `last_frame` | keyframe 模式图片引用；安全公网 URL 可直接使用，本地资产需配置 Reference Relay；至少一个 |
| `images` | reference 模式图片引用数组，网关当前最多 8 张；本地资产需配置 Reference Relay |

2.5 明确不使用 v2.0 的 `width`、`height`、`num_frames`、`frame_rate`、`num_inference_steps`。不要混发两套参数。

### 参考图交付边界

Agnes Video 2.5 上游最终需要自己抓取公开 `http(s)` 图片。安全公网 URL 会直接通过 Provider reference 校验；网关自有 `/uploads/*`、`/generated/*` 或安全 data URL 在配置 Reference Relay 后，会由 AngeMedia 发布成短时公网 URL再提交。Relay 未配置时，本地引用会 fail-fast，不会把受保护的本地地址直接暴露给上游。

如果部署不希望配置 Relay，又需要使用 AngeMedia 本地受保护资产，可以显式选择兼容模型 `agnes-video-v2.0`，沿用网关直接物化路径。

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

## 四、Agnes Video v2.0 兼容请求

未显式指定视频模型时使用 `agnes-video-v2.0`。若账号仍可调用该型号，可以直接引用网关自有的图片资产，无需配置 Reference Relay。

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

- 提交时收到明确的 `video_queue_full` 且没有任务号，表示本次未受理。普通 503 且没有任务号时，先核对渠道任务和扣费记录。两种情况均不会自动重提。
- reference/keyframe 无法交付：先检查公网 URL 是否通过安全校验；本地资产则检查 Reference Relay 是否已配置，或改用 v2.0 的直接物化路径。
- 已拿到 `job_id`：不要自行做高频 Provider 轮询。


## 2.5 系列参考素材

模型权限和额度以渠道账号为准。

| 型号 | 尺寸 | 图片 | 音频 | 视频 | 参考总数 |
|---|---|---|---|---|---|
| `agnes-video-2.5` | 720P/1080P/1K/2K | 8 | 3 | 1 | 12 |
| `agnes-video-2.5-flash` | 仅720P | 5 | 3 | 不支持 | 8 |

共用text/keyframe/reference三种模式、4–12秒、n=1。reference可只提供音频；keyframe只能使用首尾帧。图片、音频、视频不可被静默舍弃；未知请求字段返回校验错误。音频使用`audios: ["https://example.com/a.mp3"]`；标准版视频使用`videos: [{"url": "https://example.com/v.mp4", "start_seconds": 0, "require_audio": false}]`。音频/视频仅支持公开HTTP(S)地址，不提供本地上传或音视频relay；时长、文件大小、帧率及音轨由渠道验证。

提交`POST /v1/videos`后持久化响应`video_id`，所有2.5模式查询均带实际型号`model_name`，Flash不能发送标准版型号。沿用现有outbox/worker/资产导入，提交结果不确定时不自动重提。

图片本地资产继续使用现有Reference Relay；缺少relay时明确失败。界面切换型号保留音视频输入，不支持的输入阻止提交，可用清空参考媒体按钮移除。超过图片数量上限会报错，不再截断。

渠道额度、费用及限制请查阅官方文档。[Flash](https://www.agnes-ai.com/zh-Hans/docs/agnes-video-25-flash) · [标准版](https://www.agnes-ai.com/zh-Hans/docs/agnes-video-25)
