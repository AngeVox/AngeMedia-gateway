# AngeMedia Gateway

[English](README.md) | [简体中文](README_CN.md)

OpenAI-compatible image and video generation gateway for AI agents, New-API, NAS, and self-hosted media workflows.

AngeMedia is the media-generation sibling of AngeVoice. It focuses on safe image/video routing, queued worker execution, local media delivery, and a lightweight Web Studio for operators.

## Release

Current release target: `v0.2.1`

This release includes:

- OpenAI-compatible image generation: `POST /v1/images/generations`
- Async video tasks: `POST /v1/videos`, `GET /v1/videos/{task_id}`
- SQLite job truth with lifecycle, events, attempts, dispatches, and request dedupe
- Redis/Celery queue runtime with dispatcher and worker processes
- Web Studio for Dashboard, Generate Image, Generate Video, Jobs, Assets, Channels, API keys, Diagnostics, and Assistant settings
- Jobs Task Center with server-side filters, safe job detail, events, attempts, diagnostics, and linked assets
- Dashboard and Assets integration with queued job summaries
- Prompt Copilot and AngeMedia Assistant with safe LLM fallback behavior
- Local media import to controlled `/generated/*` and `/uploads/*` paths

## Architecture

```text
AI Agent / New-API / OpenAI SDK / AngeMedia Studio
        |
        v
AngeMedia Gateway API
        |
        v
SQLite jobs + job_events + job_attempts + job_dispatches
        |
        v
Dispatcher -> Redis/Celery -> Worker runtime
        |
        v
Image / video provider adapters
        |
        v
Local Assets under /generated or /uploads
```

Redis is only the broker. SQLite remains the business source of truth.

## Quick Start

```bash
git clone https://github.com/AngeVox/angemedia-gateway.git
cd angemedia-gateway

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export ADMIN_USERNAME=admin
export ADMIN_DEFAULT_PASSWORD='replace-with-a-long-random-password'
python -m uvicorn scripts.angemedia_gateway.server:app --host 127.0.0.1 --port 9890
```

Open:

```text
http://localhost:9890/studio
```

Health check:

```bash
curl http://localhost:9890/health
```

## Queue Runtime

For real queued generation, run API, Redis, dispatcher, and worker together.

```bash
docker compose up -d redis
python -m uvicorn scripts.angemedia_gateway.server:app --host 127.0.0.1 --port 9890
python -m angemedia_gateway.cli.dispatcher
python -m angemedia_gateway.cli.worker --loglevel INFO
```

The compose smoke gate verifies the same architecture without calling real providers:

```bash
python scripts/ci/queue_compose_smoke.py --dry-run
```

Run the real smoke only on a machine with Docker available:

```bash
python scripts/ci/queue_compose_smoke.py
```

## Configuration

Set only the channels you intend to use. Provider keys are never baked into the image.

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

`ANGE_LLM_MODEL` intentionally has no hard-coded GPT placeholder. Configure it from Studio Assistant Settings or environment variables.

## Docker Compose

The production container listens on port `8000`; the included compose file maps host port `9892` to `8000`.

```bash
export ADMIN_USERNAME=admin
export ADMIN_DEFAULT_PASSWORD='replace-with-a-long-random-password'
export GATEWAY_API_KEY='replace-with-a-long-random-api-key'
docker compose up -d --build
```

Open:

```text
http://localhost:9892/studio
```

Runtime data is persisted in named volumes mounted at `/app/state`, `/app/generated`, and `/app/uploads`.

## API Examples

Generate an image:

```bash
curl -X POST http://localhost:9890/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a cinematic orange cat wearing sunglasses, neon city background","size":"1024x1024"}'
```

Submit a video task:

```bash
curl -X POST http://localhost:9890/v1/videos \
  -H "Content-Type: application/json" \
  -d '{"prompt":"A cinematic shot of a cat walking through a neon rainy street","num_frames":121,"frame_rate":24}'
```

Route before generation:

```bash
curl -X POST http://localhost:9890/v1/media/route \
  -H "Content-Type: application/json" \
  -d '{"prompt":"画一张现实风格的美女写真"}'
```

If `GATEWAY_API_KEY` is configured, include it in API-mode requests:

```http
Authorization: Bearer <GATEWAY_API_KEY>
```

Gateway API keys cannot access Admin Session APIs.

## Web Studio

Studio is available at:

```text
GET /studio
GET /
```

Main surfaces:

- Dashboard: queue state, recent jobs, recent failures, recent assets, storage summary
- Generate Image: channel/model/size selection, Prompt Copilot, queued submission, result preview
- Generate Video: text-to-video and image-to-video queued submission, result preview
- Jobs: server-side filters, pagination, safe detail drawer, events, attempts, diagnostics, linked assets
- Assets: local generated/uploaded assets with job/generation summaries
- Channels: image/video channel categories, runtime configuration, connection tests
- Diagnostics: safe runtime, queue, media, and failure diagnostics
- Assistant: scoped AngeMedia assistant with shared LLM settings and local fallback
- API Keys: create and revoke API-mode keys

The Studio is plain static JavaScript. It does not introduce React, Vite, TypeScript, or a build step.

## Channels

Built-in image channels include SiliconFlow, ModelScope, Pollinations, OpenAI-compatible Image, ByteDance Seedream, and Agnes Image. Availability depends on runtime configuration and catalog status.

Built-in video support is currently centered on Agnes Video. Additional video channel adapters may exist in registry/internal code, but unverified channels are not exposed as default release choices.

Terminology in the UI uses "Channel"; the backend still uses the historical `provider` field for compatibility.

## Assistant and Prompt Copilot

AngeMedia Assistant is scoped to AngeMedia Gateway / Studio / queue / generation / channels / assets / diagnostics. It can use the configured OpenAI-compatible LLM endpoint when enabled, and falls back to local guidance when the LLM is unavailable.

Prompt Copilot is available from Generate Image and Generate Video. It returns user-facing guidance plus an English model prompt. Chinese users see Chinese explanations, but generation prompts sent to models remain English unless the user intentionally writes otherwise.

The assistant does not expose API keys, Authorization headers, raw provider bodies, request hashes, signed URLs, data URLs, or local filesystem paths.

## Security Notes

- Admin APIs require an HttpOnly Admin Session.
- Gateway API keys are limited to API-mode generation and protected media access.
- `/generated/*` and `/uploads/*` are controlled media paths and require authentication.
- Provider signed URLs are downloaded server-side and localized before being shown to the UI.
- Jobs and Dashboard presenters return safe summaries, not raw `input_json`, `output_json`, request hashes, or provider bodies.
- Local filesystem paths are not returned in public/admin UI summaries.
- Real provider keys must be supplied through local runtime configuration only.

## Agent Skill

Agent-facing skill files live in:

```text
skill/
```

This keeps agent instructions focused on image/video generation calls and avoids loading full Web Studio or development documentation into the model context.

Important docs:

- Main skill index: `SKILL.md`
- Image generation: `docs/SKILL_IMAGE_GENERATION.md`
- Video generation: `docs/SKILL_VIDEO_GENERATION.md`
- Routing: `docs/SKILL_MEDIA_ROUTING.md`
- Prompt guidance: `docs/SKILL_PROMPT_ENHANCEMENT.md`
- Assistant output schema: `docs/ANGE_ASSISTANT_OUTPUT_SCHEMA.md`

## Release Packaging

Release workflow builds code and skill archives:

```text
angemedia-gateway-<version>.zip
angemedia-gateway-skill-<version>.zip
```

Do not include local runtime directories, generated media, SQLite databases, logs, `.env`, `.codex`, `.agent`, `.pytest_cache`, `.playwright-output`, or `output/` in release archives.

## Compatibility

The backend compatibility entry remains:

```text
scripts/proxy.py
```

The real implementation lives under:

```text
scripts/angemedia_gateway/
```

## License

Apache-2.0 License.
