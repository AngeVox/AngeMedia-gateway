# AngeMedia Gateway

> OpenAI-compatible image and video generation gateway for AI Agents, NAS, New-API, and self-hosted workflows.

AngeMedia is the media-generation sibling of AngeVoice. It focuses on images, video, model routing, prompt enhancement, and stable local media delivery.

## What it does

```text
AI Agent / New-API / OpenAI SDK / AngeMedia Studio
        ↓
AngeMedia Gateway
        ↓
SiliconFlow Kolors → ModelScope Qwen / FLUX / Z-Image → Pollinations
        ↓
Agnes image/video and OpenAI-compatible image providers when explicitly selected
        ↓
Stable local /generated/ URL
```

## Highlights

- OpenAI-compatible image endpoint: `POST /v1/images/generations`
- Video task endpoints: `POST /v1/videos`, `GET /v1/videos/{task_id}`
- Built-in media localization: temporary remote URLs are downloaded into `/generated/`
- Lightweight route API: `POST /v1/media/route`
- Lightweight prompt enhancement API: `POST /v1/prompt/enhance`
- Agent Skill docs for image/video generation, routing, and prompt enhancement
- Built-in AngeMedia Studio web UI at `/`

## Default image chain

```text
kolors → qwen → flux → z-image → z-turbo → pollinations
```

Optional explicit providers:

- `agnes-image`, `agnes-2.1`, `agnes-2.0`
- `gpt-image-2`, `openai-image`
- `agnes-video-v2.0` through `/v1/videos`

## Quick start

```bash
git clone https://github.com/ang77712829/angemedia-gateway.git
cd angemedia-gateway

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
python3 scripts/proxy.py
```

Open:

```text
http://localhost:9890/
```

Health check:

```bash
curl http://localhost:9890/health
```

## Configuration

Fill at least one of these in `.env`:

```env
SILICONFLOW_API_KEY=
MODELSCOPE_API_KEY=
POLLINATIONS_API_KEY=
```

Optional:

```env
AGNES_API_KEY=
OPENAI_IMAGE_API_KEY=
GATEWAY_API_KEY=
```

For full Chinese setup instructions, see `README_CN.md`.

## API quick examples

Generate image:

```bash
curl -X POST http://localhost:9890/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a cinematic orange cat wearing sunglasses, neon city background","size":"1024x1024"}'
```

Submit video task:

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

## Skill docs

- Main skill index: `SKILL.md`
- Image generation: `docs/SKILL_IMAGE_GENERATION.md`
- Video generation: `docs/SKILL_VIDEO_GENERATION.md`
- Routing: `docs/SKILL_MEDIA_ROUTING.md`
- Prompt enhancement: `docs/SKILL_PROMPT_ENHANCEMENT.md`
- Agnes examples: `docs/AGNES_MODEL_CALL_EXAMPLES.md`

## Version

AngeMedia Gateway starts fresh at `v0.1.0`.

The previous experimental name was Image Proxy Gateway. There is no compatibility promise for the old name because the project had no public users yet.

## License

MIT


## Web UI

- Studio: `GET /` or `GET /studio`
- Admin: `GET /admin`
- API docs: `GET /api-docs`

The Studio keeps the generation workflow focused: it shows a breathing pixel-assembly animation while media is being generated, fades the finished image/video into the preview area, keeps raw JSON collapsed behind a debug toggle, and provides browser downloads for generated media. It also includes a browser-side generation queue so repeated clicks do not submit duplicate jobs. When the Ange assistant is enabled, it surfaces concrete plan details, prompt changes, work steps, and an inline confirm-and-execute action before generation. Provider state, model alias lists, and grouped Chinese configuration controls live in the Admin page.

The Admin configuration center groups settings by purpose: basic gateway/localization, built-in image channels, Agnes, OpenAI-compatible image, custom providers, and the optional Ange assistant. Custom OpenAI-compatible image providers can be added multiple times, sorted, tested, enabled/disabled, and deleted. The assistant page can pull model lists and test LLM connectivity. Human-facing labels and descriptions are localized; environment variable names remain visible only as developer identifiers.


## v0.1.0 expanded local runtime

This version also includes a small local runtime layer:

- SQLite memory database: `ANGEMEDIA_DB_FILE`
- Provider configuration management: `/v1/admin/config`
- Provider configuration metadata: `/v1/admin/config-metadata`
- Provider status/templates and custom provider operations: `/v1/admin/provider-status`, `/v1/admin/provider-templates`, `/v1/admin/providers/*`
- Assistant model pull and connectivity test: `/v1/admin/assistant/models`, `/v1/admin/assistant/test`
- Generation history: `/v1/history`
- Video task queue: `/v1/video-tasks`
- Upload manager for multi-image references: `/v1/uploads`
- Optional Ange assistant:
  - `POST /v1/assistant/plan`
  - `POST /v1/assistant/generate`

The built-in assistant uses OpenAI-compatible chat completions when configured. If it is disabled or not configured, AngeMedia falls back to the rule-based route and prompt enhancement logic.

Assistant configuration keys:

```env
ANGE_ASSISTANT_ENABLED=false
ANGE_LLM_API_KEY=
ANGE_LLM_BASE_URL=https://api.openai.com/v1
ANGE_LLM_MODEL=gpt-4o-mini
ANGE_LLM_TEMPERATURE=0.35
ANGE_LLM_TIMEOUT=60
ANGE_ASSISTANT_ALLOW_PAID=false
ANGE_ASSISTANT_ALLOW_AGNES=true
ANGE_ASSISTANT_CONFIRM_PLAN=false
```


## Modular backend layout

The legacy entry remains:

```text
scripts/image-gateway/gateway.py
```

The real backend implementation now lives in:

```text
scripts/angemedia_gateway/
```

This splits configuration, configuration metadata, SQLite state, schemas, media localization, routing, assistant logic, providers, and FastAPI route assembly. `server.py` now only assembles the app; page, admin, media, and storage routes live under `scripts/angemedia_gateway/routes/`.


## Standalone Agent Skill package

Agent-facing skill files live in:

```text
skill/
```

This keeps the agent skill small and focused on image/video generation calls, without loading the full web/admin/project documentation.

Release workflow builds both:

```text
angemedia-gateway-<version>.zip
angemedia-gateway-skill-<version>.zip
```


## Admin login

The admin panel uses username/password login by default:

```text
username: admin
password: admin123456
```

The password is stored as a PBKDF2 hash in SQLite, not as plaintext. Change it immediately after first login in production.

```env
ADMIN_USERNAME=admin
ADMIN_DEFAULT_PASSWORD=admin123456
ADMIN_COOKIE_SECURE=false
```


## Docker frontend assets

The Dockerfile copies the `app/` directory, so Studio, Admin, and API docs pages are available in container deployments.


## Security notes

- Admin login has a basic rate limit: 5 failed attempts lock the user/IP pair for 30 seconds.
- Generated gateway keys saved by the admin panel are no longer returned as plaintext by default.
- Docker image includes HEALTHCHECK.
- Admin APIs support HttpOnly cookie auth and still accept `GATEWAY_API_KEY`.

## Security note: GATEWAY_API_KEY

When `GATEWAY_API_KEY` is empty, AngeMedia allows local/LAN clients to call image and video generation APIs without an API key. This is intentional for self-hosted local deployments.

For public deployments, always configure `GATEWAY_API_KEY`, use HTTPS reverse proxy protection, and change the default admin password.

- `UPLOAD_MAX_FILES` controls how many files `/v1/uploads` accepts in one request; default is 10. Remote media localization validates the original URL and every redirect target to reduce SSRF risk.
