# Agnes 视频模型调用示例

> 这里单独放 Agnes 视频能力，避免把视频的异步任务、轮询、帧数、关键帧说明塞进 `SKILL.md`。
> 官方文档入口：`https://agnes-ai.com/doc`。

## 一、视频调用入口

```text
POST /v1/videos
GET  /v1/videos/{task_id}
```

## 二、文生视频

```bash
curl -X POST http://localhost:9890/v1/videos \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agnes-video-v2.0",
    "prompt": "一只橘猫戴着墨镜走过霓虹灯街道，电影感镜头，雨夜反光，缓慢推进镜头。",
    "width": 1152,
    "height": 768,
    "num_frames": 121,
    "frame_rate": 24
  }'
```

## 三、图生视频

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "让画面中的人物缓慢转头，背景光影轻微移动，电影感，动作自然。",
  "image": "https://example.com/input.jpg",
  "width": 1152,
  "height": 768,
  "num_frames": 121,
  "frame_rate": 24
}
```

## 四、多图 / 关键帧视频

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "从第一张图平滑过渡到第二张图，镜头自然推进，光影连续，电影感。",
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

## 五、查询任务状态

```bash
curl http://localhost:9890/v1/videos/<task_id>
```

## 六、视频 URL 字段说明

Agnes 实测完成后可能把视频地址放在 `remixed_from_video_id` 字段。网关会把 `video_url`、`remixed_from_video_id`、`url`、`output_url` 这些常见字段统一归一化到 `video_url`。

## 七、同步等待完成

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "未来城市上空的无人机航拍镜头，霓虹灯，雨夜，电影感。",
  "width": 1152,
  "height": 768,
  "num_frames": 121,
  "frame_rate": 24,
  "wait_for_completion": true
}
```

## 八、常用参数建议

| 用途 | num_frames | frame_rate | 大致时长 |
|---|---:|---:|---:|
| 很短的动图/测试 | 81 | 24 | 约 3.4 秒 |
| 常规短视频 | 121 | 24 | 约 5 秒 |
| 中等长度 | 241 | 24 | 约 10 秒 |
| 长一点的片段 | 441 | 24 | 约 18.4 秒 |

`num_frames` 一般使用 `8n+1` 形式，常见值：`81`、`121`、`161`、`241`、`441`。当前网关上限为 `441` 帧；默认 `24fps` 下约 `18.4` 秒。
