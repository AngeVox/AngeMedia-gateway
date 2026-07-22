# AngeMedia Gateway

[English](README.md) | [简体中文](README_CN.md)

AngeMedia Gateway 是一个面向 AI Agent、OpenAI-compatible 客户端、New API、自托管 NAS 和私有媒体工作流的图片/视频生成网关。

它提供稳定的生成接口、渠道路由、队列执行、受保护本地媒体存储，以及面向运维和创作者的 Web Studio。

## 项目简介

AngeMedia Gateway 把多家图片和视频生成渠道收口到同一套 API 与 Web Studio 中。它适合本地部署、私有集成、Agent 工具调用和需要统一管理生成任务的团队使用。

## 核心功能

- OpenAI-compatible 图片生成接口：`POST /v1/images/generations`。
- 异步视频任务接口：`POST /v1/videos`，状态查询：`GET /v1/videos/{task_id}`。
- 面向成本与能力的图片路由：SiliconFlow Kolors、ModelScope、Pollinations、OpenAI-compatible Image、ByteDance Seedream 和显式 Agnes Image 渠道。
- 稳定图生图路径：通过 SiliconFlow/Kolors 处理带参考图的图片生成。
- 当前视频主路径：Agnes Video，支持文生视频、图生视频和首尾帧风格提交，并兼容新版任务轮询与 `metadata.url` 结果结构。
- fnOS/FYGO 离线包同时支持 x86_64 与 ARM64；应用设置提供带数据库备份的管理员凭据灾备重置。
- DockerHub 发布镜像使用同一个多架构清单，同时支持 `linux/amd64` 与 `linux/arm64`。
- Redis/Celery worker 队列执行，任务状态持久化。
- 生成媒体本地化到受控 `/generated/*` 和 `/uploads/*` 路径。
- Web Studio 覆盖生成、任务、资产、渠道、诊断、API 密钥和小助手设置。
- Prompt Copilot 与 AngeMedia 小助手提供限定范围内的媒体规划和排障。

## 快速开始

```bash
git clone https://github.com/AngeVox/angemedia-gateway.git
cd angemedia-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
export ADMIN_USERNAME=admin
export ADMIN_DEFAULT_PASSWORD='换成足够长的随机密码'
python -m uvicorn scripts.angemedia_gateway.server:app --host 127.0.0.1 --port 9890
```

`requirements.lock` 是 v0.2.11 验证过的可复现依赖集合。只有在主动刷新依赖范围时才使用 `requirements.txt`。

打开 Web Studio：

```text
http://localhost:9890/studio
```

健康检查：

```bash
curl http://localhost:9890/health
```

## Docker Compose

仓库内 Compose 栈会启动 gateway、Redis、dispatcher 和 worker。容器监听 `8000`，默认 Compose 映射宿主端口 `9892` 到 `8000`。
正式发布镜像同时支持 `linux/amd64` 与 `linux/arm64`，Docker 会自动选择当前设备对应的架构。

```bash
export ADMIN_USERNAME=admin
export ADMIN_DEFAULT_PASSWORD='换成足够长的随机密码'
export GATEWAY_API_KEY='换成足够长的随机网关密钥'
docker compose up -d --build
```

打开 Web Studio：

```text
http://localhost:9892/studio
```

运行数据通过命名卷挂载到容器内 `/app/state`、`/app/generated` 和 `/app/uploads`。

## 配置

可以复制 `.env.example`，也可以直接设置环境变量。只配置你实际要用的渠道。

```env
ADMIN_USERNAME=admin
ADMIN_DEFAULT_PASSWORD=换成足够长的随机密码
GATEWAY_API_KEY=换成足够长的随机网关密钥

SILICONFLOW_API_KEY=
MODELSCOPE_API_KEY=
POLLINATIONS_API_KEY=
AGNES_API_KEY=

OPENAI_IMAGE_API_KEY=
OPENAI_IMAGE_BASE_URL=https://api.openai.com/v1
OPENAI_IMAGE_MODEL=gpt-image-2

ANGE_LLM_ENABLED=false
ANGE_LLM_BASE_URL=
ANGE_LLM_API_KEY=
ANGE_LLM_MODEL=

IMAGE_PROVIDER_TIMEOUT=300
VIDEO_PROVIDER_TIMEOUT=900
```

Provider credential 是运行时密钥。不要提交真实密钥、本地数据库、生成媒体或 `.env.*` 文件。

## API 示例

生成图片：

```bash
curl -X POST http://localhost:9890/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a cinematic orange cat wearing sunglasses on a neon city street","size":"1024x1024","response_format":"url"}'
```

通过 SiliconFlow/Kolors 做图生图：

```bash
curl -X POST http://localhost:9890/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"kolors","prompt":"keep the main subject and composition, convert the image into a clean cinematic poster style","image":"https://example.com/input.png","size":"1024x1024","response_format":"url"}'
```

提交 Agnes Video 任务：

```bash
curl -X POST http://localhost:9890/v1/videos \
  -H "Content-Type: application/json" \
  -d '{"model":"agnes-video-v2.0","prompt":"A cinematic shot of a cat walking through a neon rainy street, smooth camera tracking, filmic lighting.","width":1152,"height":768,"num_frames":121,"frame_rate":24}'
```

生成前路由：

```bash
curl -X POST http://localhost:9890/v1/media/route \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Create a realistic commercial portrait with neon city lighting"}'
```

配置 `GATEWAY_API_KEY` 后，API-mode 请求应带：

```http
Authorization: Bearer <GATEWAY_API_KEY>
```

Gateway API Key 不是管理员会话凭据。

## Web Studio

Web Studio 入口是 `GET /studio` 和 `GET /`。

- Dashboard：队列状态、最近任务、失败、资产和存储摘要。
- 生成图片：渠道、模型、操作、尺寸、参考图、Prompt Copilot 和结果预览。
- 生成视频：文生视频、图生视频和首尾帧风格的 Agnes Video 提交。
- 任务：分页状态、安全详情、事件、尝试、诊断和关联资产。
- 资产：生成或上传的媒体，以及任务和模型摘要。
- 渠道：内置和自定义渠道配置、连接测试。
- 诊断：运行时、队列、媒体和失败摘要。
- 小助手：限定在 AngeMedia 范围内的帮助，并可配置 OpenAI-compatible LLM。
- API 密钥：创建和撤销 API-mode key。

Studio 是静态 JavaScript，不需要前端构建步骤。

## 渠道

图片渠道包括：

- SiliconFlow/Kolors：默认链图片生成，以及稳定图生图路径。
- ModelScope：Qwen Image、FLUX Krea、Z-Image 和 Z-Image Turbo。
- OpenAI-compatible Image：显式高质量付费或私有端点。
- Pollinations 和 ByteDance Seedream：可选实验渠道。
- Agnes Image：显式 Agnes 图片模型渠道；需要有意选择，不作为默认图生图承诺。

视频生成当前以 Agnes Video v2.0 为主。其他视频适配器应视为后续渠道工作，只有具备真实 adapter、catalog entry 和测试后才应作为可用渠道呈现。

UI 面向用户使用“渠道”；后端仍兼容历史 `provider` 字段。

## 安全说明

- Admin API 需要 HttpOnly 管理员会话。
- Gateway API Key 只用于 API-mode 生成和受保护媒体访问。
- `/generated/*` 和 `/uploads/*` 是受控媒体路径，需要认证访问。
- Provider signed URL 会由服务端下载并本地化后再返回。
- 公开和管理摘要不返回 raw provider response、request hash、signed URL、data URL、API key、Authorization header 或本地文件系统路径。
- 真实 provider key 只应存在于本地运行配置中。

## 更多文档

- [CHANGELOG.md](CHANGELOG.md)：版本记录。
- [SKILL.md](SKILL.md)：Agent skill 入口。
- [docs/SKILL_IMAGE_GENERATION.md](docs/SKILL_IMAGE_GENERATION.md)：图片生成说明。
- [docs/SKILL_VIDEO_GENERATION.md](docs/SKILL_VIDEO_GENERATION.md)：视频生成说明。
- [docs/SKILL_MEDIA_ROUTING.md](docs/SKILL_MEDIA_ROUTING.md)：渠道和模型路由。
- [docs/SKILL_PROMPT_ENHANCEMENT.md](docs/SKILL_PROMPT_ENHANCEMENT.md)：提示词增强规则。
- [docs/MODEL_RESOLUTION_REFERENCE.md](docs/MODEL_RESOLUTION_REFERENCE.md)：尺寸和分辨率参考。
- [docs/AGNES_VIDEO_CALL_EXAMPLES.md](docs/AGNES_VIDEO_CALL_EXAMPLES.md)：Agnes Video 示例。
- [docs/VIDEO_ADAPTER_DESIGN.md](docs/VIDEO_ADAPTER_DESIGN.md) 和 [docs/video_channels/ADAPTER_CONTRACT.md](docs/video_channels/ADAPTER_CONTRACT.md)：视频适配器贡献说明。
- [docs/ANGE_ASSISTANT_OUTPUT_SCHEMA.md](docs/ANGE_ASSISTANT_OUTPUT_SCHEMA.md)：小助手输出 schema。

`docs/assistant/` 下是应用运行时小助手资源，不是第二套公开 skill 包。可分发的 Agent skill 包位于 `skill/`。

## License

Apache-2.0 License。
