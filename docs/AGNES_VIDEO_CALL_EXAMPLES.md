# Agnes 视频模型调用示例

> 这里单独放 Agnes 视频能力，避免把视频的异步任务、状态查询、帧数、关键帧说明塞进 `SKILL.md`。
> 官方文档入口：`https://agnes-ai.com/zh-Hans/docs/overview`。

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

## 五、状态查询（非 Agent 主动轮询）

```bash
curl http://localhost:9890/v1/videos/<task_id>
```

这个接口用于 Web Studio、人工排查或明确需要查询单个任务状态的场景。Agent 提交异步视频任务后，应提示用户到 Web Studio Jobs / Assets 查看结果，不应主动轮询到完成。

## 六、视频 URL 字段说明

Agnes 当前完成响应会把视频地址放在 `metadata.url`。网关优先兼容旧字段 `video_url`、`remixed_from_video_id`、`url`、`output_url`，并读取新版 `metadata.url`，统一归一化后再执行安全下载和本地资产导入。任务查询优先使用当前 `GET /agnesapi?video_id=...` 端点，并仅对兼容性状态码回退旧路径。

## 七、同步等待完成（非推荐）

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


## 九、上游繁忙与 503

提交阶段若返回 HTTP 503 且没有任务 ID，Web Studio 会提示视频服务商繁忙。为避免上游实际已接受请求时发生重复扣费或重复生成，网关不会自动重提；请稍后手动重新提交。
