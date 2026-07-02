---
name: angemedia-gateway
description: "当用户表达生成图片、画图、文生图、图生图、生成封面/海报/头像，或表达文生视频、图生视频、关键帧视频，并且需要通过 AngeMedia Gateway 调用图片/视频生成接口时使用。优先按意图触发，而不是只靠工具名触发。"
version: v0.2.1
compatible_gateway: ">=v0.2.1 <v0.3.0"
author: 安歌 & 辰辰
license: Apache-2.0
metadata:
  hermes:
    tags: [image-generation, video-generation, openai-compatible, ai-agent, prompt-routing, model-routing]
---

# AngeMedia Gateway Agent Skill

这个 Skill 只负责指导 Agent 如何调用 AngeMedia Gateway 生成图片和视频。  
它只保留接口调用、模型路由和提示词增强规则，不包含部署、前端或项目开发说明，避免干扰 Agent 的生成注意力。

## 一、触发条件

满足任一情况就使用本技能：

- 用户要求生成图片、画图、文生图、图生图、头像、封面、海报、商品图；
- 用户要求生成视频、文生视频、图生视频、关键帧视频、让图片动起来；
- 用户提示词很短，需要 Agent 先补全和优化；
- 用户明确要求通过 AngeMedia Gateway、OpenAI-compatible 图片接口或本地媒体生成网关生成。

## 二、Agent 主流程

1. 判断任务类型：图片还是视频。
2. 判断输入模式：纯文本、单图、首尾帧、多参考图。
3. 做模型路由：优先成本低且适配的模型，不盲目使用最贵模型。
4. 做提示词增强：短提示词扩写，详细提示词只轻度整理。
5. 调用 AngeMedia Gateway。
6. 优先使用返回的本地化 URL 或本地文件路径。

## 三、接口入口

图片：

```text
POST /v1/images/generations
```

视频：

```text
POST /v1/videos           # 提交视频任务
GET  /v1/videos/{task_id} # 查询任务状态（供 Web Studio 使用，Agent 不应主动轮询）
```

路由：

```text
POST /v1/media/route
```

提示词整理由 Agent 在调用生成接口前完成；当前 v0.2.1 Skill 不依赖普通提示词增强或小助手生成路由。

如果配置了 `GATEWAY_API_KEY`，请求要带：

```http
Authorization: Bearer <GATEWAY_API_KEY>
```

或：

```http
X-API-Key: <GATEWAY_API_KEY>
```

## 四、模型路由速查

默认免费链：

```text
kolors → qwen → flux → z-image → z-turbo
```

`pollinations` 为实验性渠道，默认关闭，不在默认降级链中。需要在 `.env` 中手动启用后，显式指定 `model: "pollinations"` 才会调用。

| 别名 | 适合场景 |
|---|---|
| `kolors` | 默认通用图片 |
| `qwen` / `qwen-image` | 中文海报、二次元、带字图片 |
| `flux` / `flux-krea` | 产品图、风景、自然光、家居 |
| `z-image` | 创意概念、超现实 |
| `z-turbo` | 写实人像、商业摄影、真人写真 |
| `pollinations` | 实验性，默认关闭，需手动启用 |
| `agnes-2.1` / `agnes-2.0` | Agnes 图片、图生图、编辑实验 |
| `gpt-image-2` | 显式付费高质量图片 |
| `agnes-video-v2.0` | 文生视频、图生视频、首尾帧视频 |

## 五、图片最小请求

```json
{
  "prompt": "一只戴墨镜的猫，赛博朋克风格，霓虹灯背景，电影感光影",
  "size": "1024x1024",
  "response_format": "url"
}
```

指定模型：

```json
{
  "model": "z-turbo",
  "prompt": "现实风格人像写真，雨夜霓虹街头，电影感，浅景深",
  "size": "1024x1024",
  "response_format": "url"
}
```

图生图 / 参考图：

```json
{
  "model": "agnes-2.1",
  "prompt": "保持人物身份和构图，改成电影海报风格",
  "image": "https://example.com/input.png",
  "size": "1024x1024",
  "response_format": "url"
}
```

## 六、视频最小请求

异步提交：

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "一只猫在雨夜霓虹街头缓慢前进，镜头平滑跟随，电影感",
  "width": 1152,
  "height": 768,
  "num_frames": 121,
  "frame_rate": 24,
  "wait_for_completion": false
}
```

提交后返回 `job_id` / `task_id`，提示用户稍后到 Web Studio 的 Jobs / Assets 页面查看结果。Agent 不应轮询 API。

图生视频：

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "让画面中的人物慢慢回头，头发被微风吹动，镜头稳定",
  "image": "https://example.com/first-frame.png",
  "width": 1152,
  "height": 768,
  "num_frames": 121,
  "frame_rate": 24
}
```

首尾帧视频：

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "从第一张图平滑过渡到第二张图，镜头运动自然，画面无跳变",
  "images": [
    "https://example.com/first.png",
    "https://example.com/last.png"
  ],
  "mode": "keyframes",
  "width": 1152,
  "height": 768,
  "num_frames": 121,
  "frame_rate": 24
}
```

## 七、提示词增强原则

- 用户提示词很短：补主体、场景、构图、光影、风格、质感、负面限制。
- 用户提示词很详细：只做整理，不擅自改核心内容。
- 用户写了“不要文字 / 不要人物 / 保留构图”等限制：必须保留。
- 视频要补运动、镜头、节奏、时间连续性，不要只写静态画面。

## 八、返回结果使用规则

优先级：

1. 本地化后的 `url` 或 `video_url`
2. `/generated/*` 或 `/uploads/*` 受控路径
3. 仅在宿主系统内部使用远端临时 URL，不展示给用户

如果响应里有：

```json
"localized": false
```

说明本地化失败或未开启。Agent 应提醒用户远端 URL 可能过期。


## 九、兼容网关版本

本 Skill 兼容 AngeMedia Gateway `>=v0.2.1 <v0.3.0`。如果网关接口升级到 v0.3 或更高版本，应同步更新 Skill 包。
