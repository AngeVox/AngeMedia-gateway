# Ange 小助手输出 Schema

内置小助手应输出 JSON。后端会对输出进行校正，非法字段不会直接执行。

## 图片计划

```json
{
  "media_type": "image",
  "model": "qwen | z-turbo | flux | z-image | agnes-2.1 | agnes-2.0 | gpt-image-2 | null",
  "prompt": "string",
  "size": "1024x1024",
  "response_format": "url",
  "negative_prompt": "string",
  "reason": "string"
}
```

## 视频计划

```json
{
  "media_type": "video",
  "model": "agnes-video-v2.0",
  "input_mode": "t2v | first_frame | first_last_frame | reference",
  "prompt": "string",
  "width": 1152,
  "height": 768,
  "num_frames": 121,
  "frame_rate": 24,
  "wait_for_completion": false,
  "reason": "string"
}
```

## 后端强制校正

- `num_frames` 会被校正到 `81/121/161/241/441`
- 付费模型受 `ANGE_ASSISTANT_ALLOW_PAID` 控制
- Agnes 受 `ANGE_ASSISTANT_ALLOW_AGNES` 控制
- 图片尺寸非法时回退 `1024x1024`
- 视频尺寸非法时回退 `1152x768`
