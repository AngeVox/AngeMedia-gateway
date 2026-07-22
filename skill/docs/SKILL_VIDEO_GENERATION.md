# 视频生成子技能

> 本文档处理视频任务的意图判断、输入模式判断、提示词增强和网关调用。

## 一、视频任务工作流

1. 判断是不是视频任务。
2. 判断输入模式：`t2v` / `first_frame` / `first_last_frame` / `reference`。
3. 标记每张图的角色：`first_frame`、`last_frame`、`reference`。
4. 把中文自然语言整理成适合视频模型的描述；必要时转成英文镜头语言。
5. 组装 `/v1/videos` 请求（`wait_for_completion: false`）。
6. 提交后返回 `job_id` / `task_id`，提示用户到 Web Studio 的 Jobs / Assets 页面查看结果。Agent 不应轮询 API。

v0.2.11 使用正式队列 worker 处理 Agnes 视频 submit / poll / asset import。Agent 默认提交异步任务后让用户在 Web Studio Jobs / Assets 查看状态，不应自建持续查询循环。

---

## 二、输入模式判断

### 1）T2V

只有文字，没有图。

示例：

> 生成一个未来城市夜景的视频。

### 2）first_frame

只有 1 张图，要从这张图开始动起来。

示例：

> 让这张女孩照片做一个缓慢回头的视频。

此时：

- 这张图的角色是 `first_frame`
- 生成请求通常用 `image`

### 3）first_last_frame

有开始图和结束图，要做过渡、关键帧或从 A 变到 B。

示例：

> 从第一张白天图过渡到第二张夜景图。

此时：

- 第一张图角色：`first_frame`
- 第二张图角色：`last_frame`
- 请求通常用 `images` + `mode=keyframes`

### 4）reference

有参考图，但不是严格的首尾帧，主要用来约束风格、人物、场景。

如果模型不支持强参考输入：

- 保留最关键 1 张作为 `image`
- 其余参考信息改写进 prompt

---

## 三、视频提示词增强

### 1）中文整理

先提取：

- 主体是谁
- 做什么动作
- 在什么环境里
- 光影和氛围
- 镜头怎么动
- 希望多长 / 多快 / 多平稳

### 2）转英文镜头描述

建议视频最终 prompt 用更自然的英文镜头描述。

示例：

中文：

> 让一个女孩慢慢回头，雨夜街头，有霓虹灯，镜头轻轻推进。

增强为：

> A cinematic shot of a young adult woman slowly turning her head back toward the camera on a rainy neon-lit street at night. Soft rim light, realistic skin texture, shallow depth of field, gentle camera push-in, smooth and natural motion, filmic atmosphere.

---


---

## 四、视频尺寸、帧数和时长速查

详细参考见 `docs/MODEL_RESOLUTION_REFERENCE.md`。当前 Agnes 视频网关约束是：

| 参数 | 范围 / 推荐 | 说明 |
|---|---|---|
| `width` | `256–2048`，默认 `1152` | 宽度 |
| `height` | `256–1536`，默认 `768` | 高度 |
| `num_frames` | 最大 `441`，且必须满足 `8n+1` | 常用：`81`、`121`、`161`、`241`、`441` |
| `frame_rate` | 默认 `24` | 影响时长换算 |

常用组合：

| 用途 | width × height | num_frames | frame_rate | 约时长 |
|---|---|---:|---:|---:|
| 快速测试 / 动图感 | `1152x768` | `81` | `24` | 3.4 秒 |
| 默认短视频 | `1152x768` | `121` | `24` | 5.0 秒 |
| 中等片段 | `1152x768` | `241` | `24` | 10.0 秒 |
| 最长片段 | `1152x768` | `441` | `24` | 18.4 秒 |
| 高清测试 | `2048x1536` | `81` 或 `121` | `24` | 3.4–5 秒 |

注意：`441` 帧是当前网关上限；按默认 `24fps` 约等于 `18.4` 秒。不要写成稳定 20 秒或更长。高清尺寸配长帧数会明显变慢，默认不要直接用 `2048x1536 + 441`。

## 五、推荐请求结构

### A. 文生视频

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "A cinematic aerial shot of a futuristic city at night, neon lights reflecting on wet roads, light fog, slow camera movement, rich detail, filmic atmosphere.",
  "width": 1152,
  "height": 768,
  "num_frames": 121,
  "frame_rate": 24
}
```

### B. 图生视频（单图）

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "A cinematic shot of a young adult woman slowly turning her head back toward the camera, soft rim light, subtle hair movement, shallow depth of field, smooth natural motion.",
  "image": "https://example.com/input.jpg",
  "width": 1152,
  "height": 768,
  "num_frames": 121,
  "frame_rate": 24
}
```

### C. 首尾帧视频

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "A smooth cinematic transition from the first frame to the final frame, coherent lighting, natural camera movement, consistent visual style.",
  "images": [
    "https://example.com/frame_start.jpg",
    "https://example.com/frame_end.jpg"
  ],
  "mode": "keyframes",
  "width": 1152,
  "height": 768,
  "num_frames": 121,
  "frame_rate": 24
}
```

---

## 六、异步与同步策略

### 默认：异步提交

优先提交后返回 `task_id`：

```text
POST /v1/videos
```

提交后返回 `job_id` / `task_id`，提示用户到 Web Studio 的 Jobs / Assets 页面查看结果。Agent 不应轮询 `GET /v1/videos/{task_id}`。

### 同步等待（非推荐）

`wait_for_completion=true` 会让单次请求阻塞直到视频生成完成，适合宿主环境明确支持同步等待的场景，但不作为 Agent 主推荐模式。普通场景应使用异步提交 + Web Studio 查看。

---

## 七、失败重试与降级

1. 首尾帧失败：降级为单图首帧视频。
2. 多参考失败：只保留最关键图，其余改写进 prompt。
3. 提示词过于简短：先增强再重试。
4. 返回 task_id 但迟迟未完成：提示用户到 Web Studio Jobs / Assets 查看，不要误判成失败。


---

## 七、视频本地化返回

视频生成完成后，网关默认会尝试把远端视频 URL 下载到 `OUTPUT_DIR`，并把 `video_url` 改成本地稳定地址：

```text
/generated/xxx.mp4
```

同步模式：

```json
{
  "wait_for_completion": true
}
```

完成后直接返回本地化后的 `video_url`。

异步模式：

1. `POST /v1/videos` 返回 `task_id`
2. 提示用户到 Web Studio Jobs 点击“刷新状态”并在 Assets 查看结果
3. Jobs 的 Session-only refresh API 每次最多查询 provider 一次，并有最小查询间隔；完成后触发安全本地化与 Assets 写入
4. `GET /v1/videos/{task_id}` 保留给已认证的人工状态查询
5. Agent 不应主动持续查询，也不需要额外调度任务

Agent 应优先使用本地化后的 `video_url` 给用户发送文件，不要展示远端临时 URL 或本地 filesystem path。
