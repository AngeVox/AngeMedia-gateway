# AngeMedia Gateway

[English](README.md) | [简体中文](README_CN.md)

AngeMedia Gateway is a self-hosted image and video generation gateway for AI agents, OpenAI-compatible clients, New API deployments, NAS boxes, and private media workflows.

It provides a stable API surface for generation, provider routing, queued execution, protected local media storage, and an operator-focused Web Studio.

## Features

- OpenAI-compatible image generation at `POST /v1/images/generations`.
- Asynchronous video generation at `POST /v1/videos` with status lookup at `GET /v1/videos/{task_id}`.
- Cost-aware image routing across SiliconFlow Kolors, ModelScope models, Pollinations, OpenAI-compatible image endpoints, ByteDance Seedream, and explicit Agnes Image channels.
- Stable image-to-image support through SiliconFlow/Kolors when a reference image is supplied.
- Agnes Video as the current primary video path for text-to-video, image-to-video, and keyframe-style submissions, including the current task polling and `metadata.url` result format.
- A dual-architecture fnOS/FYGO offline package for x86_64 and ARM64; package settings provide administrator credential recovery with a database backup.
- DockerHub release images publish a single multi-architecture manifest for `linux/amd64` and `linux/arm64`.
- Queue-first execution with Redis/Celery workers and persistent job state.
- Protected local media import under `/generated/*` and `/uploads/*`.
- Web Studio for generation, jobs, assets, channels, diagnostics, API keys, and assistant settings.
- Prompt Copilot and AngeMedia Assistant for scoped media planning and troubleshooting.

## Quick Start

```bash
git clone https://github.com/AngeVox/angemedia-gateway.git
cd angemedia-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
export ADMIN_USERNAME=admin
export ADMIN_DEFAULT_PASSWORD='replace-with-a-long-random-password'
python -m uvicorn scripts.angemedia_gateway.server:app --host 127.0.0.1 --port 9890
```

`requirements.lock` is the reproducible install set validated for v0.2.11. Use `requirements.txt` only when intentionally refreshing dependency ranges.

Open Web Studio:

```text
http://localhost:9890/studio
```

Check the service:

```bash
curl http://localhost:9890/health
```

## Docker Compose

The included Compose stack starts the gateway, Redis, dispatcher, and worker. The container listens on port `8000`; the default Compose file maps host port `9892` to `8000`.
Published release images support both `linux/amd64` and `linux/arm64`; Docker automatically selects the matching architecture.

```bash
export ADMIN_USERNAME=admin
export ADMIN_DEFAULT_PASSWORD='replace-with-a-long-random-password'
export GATEWAY_API_KEY='replace-with-a-long-random-api-key'
docker compose up -d --build
```

Open Web Studio:

```text
http://localhost:9892/studio
```

Runtime data is stored in volumes mounted at `/app/state`, `/app/generated`, and `/app/uploads`.

## Configuration

Copy `.env.example` or set environment variables directly. Configure only the channels you plan to use.

```env
ADMIN_USERNAME=admin
ADMIN_DEFAULT_PASSWORD=replace-with-a-long-random-password
GATEWAY_API_KEY=replace-with-a-long-random-api-key

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

Provider credentials are runtime secrets. Do not commit real keys, local databases, generated media, or `.env.*` files.

## API Examples

Generate an image:

```bash
curl -X POST http://localhost:9890/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a cinematic orange cat wearing sunglasses on a neon city street","size":"1024x1024","response_format":"url"}'
```

Generate an image from a reference image with SiliconFlow/Kolors:

```bash
curl -X POST http://localhost:9890/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"kolors","prompt":"keep the main subject and composition, convert the image into a clean cinematic poster style","image":"https://example.com/input.png","size":"1024x1024","response_format":"url"}'
```

Submit an Agnes Video task:

```bash
curl -X POST http://localhost:9890/v1/videos \
  -H "Content-Type: application/json" \
  -d '{"model":"agnes-video-v2.0","prompt":"A cinematic shot of a cat walking through a neon rainy street, smooth camera tracking, filmic lighting.","width":1152,"height":768,"num_frames":121,"frame_rate":24}'
```

Route a media prompt before generation:

```bash
curl -X POST http://localhost:9890/v1/media/route \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Create a realistic commercial portrait with neon city lighting"}'
```

When `GATEWAY_API_KEY` is configured, API-mode requests should include:

```http
Authorization: Bearer <GATEWAY_API_KEY>
```

Gateway API keys are not admin session credentials.

## Web Studio

Web Studio is available at `GET /studio` and `GET /`.

- Dashboard: queue status, recent jobs, failures, assets, and storage summary.
- Generate Image: channel, model, operation, size, references, Prompt Copilot, and result preview.
- Generate Video: text-to-video, image-to-video, and keyframe-style Agnes Video submissions.
- Jobs: paginated status, safe detail, events, attempts, diagnostics, and linked assets.
- Assets: generated and uploaded media with job and model summaries.
- Channels: built-in and custom channel configuration with connection tests.
- Diagnostics: runtime, queue, media, and failure summaries.
- Assistant: scoped AngeMedia help with optional OpenAI-compatible LLM settings.
- API Keys: create and revoke API-mode keys.

Studio is plain static JavaScript and does not require a frontend build step.

## Channels

Image channels include:

- SiliconFlow/Kolors: default-chain image generation and the stable image-to-image path.
- ModelScope: Qwen Image, FLUX Krea, Z-Image, and Z-Image Turbo.
- OpenAI-compatible Image: explicit high-quality paid or private endpoints.
- Pollinations and ByteDance Seedream: optional experimental channels.
- Agnes Image: explicit channel for Agnes image models; use it intentionally, not as the default image-to-image promise.

Video generation is currently centered on Agnes Video v2.0. Other video adapters should be treated as future channel work unless they are backed by a real adapter, catalog entry, and tests.

The UI uses "Channel" for users. The backend still accepts the historical `provider` field for compatibility.

## Security Notes

- Admin APIs require an HttpOnly admin session.
- Gateway API keys are limited to API-mode generation and protected media access.
- `/generated/*` and `/uploads/*` are controlled media paths and require authentication.
- Provider signed URLs are downloaded server-side and localized before being returned.
- Public and admin summaries avoid raw provider responses, request hashes, signed URLs, data URLs, API keys, authorization headers, and local filesystem paths.
- Real provider keys belong in local runtime configuration only.

## Documentation

- [CHANGELOG.md](CHANGELOG.md): release notes.
- [SKILL.md](SKILL.md): agent-facing skill entry.
- [docs/SKILL_IMAGE_GENERATION.md](docs/SKILL_IMAGE_GENERATION.md): image generation guidance.
- [docs/SKILL_VIDEO_GENERATION.md](docs/SKILL_VIDEO_GENERATION.md): video generation guidance.
- [docs/SKILL_MEDIA_ROUTING.md](docs/SKILL_MEDIA_ROUTING.md): channel and model routing.
- [docs/SKILL_PROMPT_ENHANCEMENT.md](docs/SKILL_PROMPT_ENHANCEMENT.md): prompt enhancement rules.
- [docs/MODEL_RESOLUTION_REFERENCE.md](docs/MODEL_RESOLUTION_REFERENCE.md): size and resolution reference.
- [docs/AGNES_VIDEO_CALL_EXAMPLES.md](docs/AGNES_VIDEO_CALL_EXAMPLES.md): Agnes Video examples.
- [docs/VIDEO_ADAPTER_DESIGN.md](docs/VIDEO_ADAPTER_DESIGN.md) and [docs/video_channels/ADAPTER_CONTRACT.md](docs/video_channels/ADAPTER_CONTRACT.md): contributor notes for video adapters.
- [docs/ANGE_ASSISTANT_OUTPUT_SCHEMA.md](docs/ANGE_ASSISTANT_OUTPUT_SCHEMA.md): assistant output schema.

Runtime assistant resources live under `docs/assistant/`; they are used by the application and are not a second public skill package. The distributable agent skill package lives under `skill/`.

## License

Apache-2.0 License.
