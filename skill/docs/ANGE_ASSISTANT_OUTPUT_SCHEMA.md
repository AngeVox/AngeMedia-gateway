# Ange 小助手输出 Schema

内置小助手输出 JSON；后端会再次校正模型、尺寸和参数，非法字段不会直接执行。

## 图片计划

```json
{
  "media_type": "image",
  "model": "qwen | z-turbo | flux | z-image | qwen-edit | agnes-image | openai-image | null",
  "prompt": "string",
  "size": "1024x1024",
  "response_format": "url",
  "negative_prompt": "string",
  "reason": "string"
}
```

`model=null` 表示使用默认免费链。付费 OpenAI 图片模型受 `ANGE_ASSISTANT_ALLOW_PAID` 控制；Agnes 受 `ANGE_ASSISTANT_ALLOW_AGNES` 控制。

## 视频计划

```json
{
  "media_type": "video",
  "model": "agnes-video-2.5",
  "input_mode": "t2v | first_frame | first_last_frame | reference",
  "mode": "text | keyframe | reference",
  "prompt": "string",
  "seconds": "5",
  "size": "720P",
  "aspect_ratio": "16:9",
  "wait_for_completion": false,
  "reason": "string"
}
```

如果规划请求带参考图片，小助手只做推荐和预填，不会绕过 Video 2.5 的公网 URL 安全边界，也不会自动提交任务。

## 后端强制校正

- Video 2.5 `seconds` 限制为字符串 `4–12`，非法值回退 `"5"`。
- Video 2.5 `size` 限制为 `720P/1080P/1K/2K`，非法值回退 `720P`。
- `aspect_ratio` 限制为当前 2.5 catalog 的六种比例，非法值回退 `16:9`。
- LLM 返回的旧 `width/height/num_frames/frame_rate` 会从当前 2.5 计划中移除。
- 图片尺寸非法时回退 `1024x1024`。
- 小助手始终是 recommendation-only；真正生成仍需用户在生成页提交。
