# AngeMedia API 模式

[English](README.md) | [简体中文](README_CN.md)

> OpenAI-compatible image and video generation gateway for AI Agents, NAS, New-API, and self-hosted workflows.

AngeMedia is the media-generation sibling of AngeVoice. It focuses on images, video, model routing, prompt enhancement, and stable local media delivery.

## What it does

```text
AI Agent / New-API / OpenAI SDK / AngeMedia Studio
        ↓
AngeMedia Gateway
        ↓
SiliconFlow Kolors → ModelScope Qwen / FLUX / Z-Image
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
- Agent Skill docs for image/video generation, routing, and prompt guidance
- Built-in AngeMedia Studio web UI at `/`
- Provider catalog API: `GET /v1/admin/catalog` — returns all built-in providers, models, capabilities, params, and size presets (admin-auth required)
- Admin account API and Studio account modal: view the current single-admin username, change username/password with `current_password`, then sign in again after the session is cleared

## Default image chain

```text
kolors → qwen → flux → z-image → z-turbo
```

Optional explicit providers:

- `agnes-image`, `agnes-2.1`, `agnes-2.0`
- `gpt-image-2`, `openai-image`
- `agnes-video-v2.0` through `/v1/videos`
- `pollinations` (experimental, disabled by default — not part of the fallback chain)

## Quick start

```bash
git clone https://github.com/ang77712829/angemedia-gateway.git
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
http://localhost:9890/
```

Health check:

```bash
curl http://localhost:9890/health
```

## Docker Compose

The production Docker entrypoint runs uvicorn on container port `8000`; the included compose file maps host port `9892` to `8000`.

Set explicit secrets before starting. Do not keep the placeholder values in production:

```bash
export ADMIN_USERNAME=admin
export ADMIN_DEFAULT_PASSWORD='replace-with-a-long-random-password'
export GATEWAY_API_KEY='replace-with-a-long-random-api-key'
docker compose up -d --build
```

Open:

```text
http://localhost:9892/
```

Health check:

```bash
curl http://localhost:9892/health
```

Runtime data is persisted in named volumes mounted at `/app/state`, `/app/generated`, and `/app/uploads`. Provider API keys are not baked into the image; enable and set only the providers you intend to use through environment variables.

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
OPENAI_IMAGE_BASE_URL=https://api.openai.com/v1
OPENAI_IMAGE_MODEL=gpt-image-2
GATEWAY_API_KEY=
ANGE_LLM_API_KEY=
ANGE_LLM_BASE_URL=https://api.openai.com/v1
ANGE_LLM_MODEL=gpt-4o-mini
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
- Prompt guidance: `docs/SKILL_PROMPT_ENHANCEMENT.md`
- Agnes examples: `docs/AGNES_MODEL_CALL_EXAMPLES.md`

## Web Studio

v0.2.0 provides a minimal Web Studio for basic administration:

**Entry points:**

- Studio: `GET /` or `GET /studio`
- Redirect: `GET /admin` → `/#/dashboard`
- Redirect: `GET /admin/` → `/#/dashboard`

**Features:**

- Dashboard: health/session summary
- Account modal: view the current single-admin username and change username/password; both changes require `current_password` and clear active sessions
- Generate Image: catalog-aware prompt input with default route, catalog model selection, and custom provider model override
- Generate Video: catalog-aware minimal video submission page (fetches providers/models/capabilities from `/v1/admin/catalog`)
- Jobs: list view of generation jobs
- Assets: list view of generated/uploaded assets with thumbnails
- Providers: custom provider onboarding and management; built-in, catalog, and reserved sections are read-only compact/folded summaries
- API Keys: list/create/revoke for API mode

**Admin login:**

```text
username: admin
password: value from ADMIN_DEFAULT_PASSWORD
```

`ADMIN_DEFAULT_PASSWORD` must be explicitly set before the first start. The password is stored as a PBKDF2 hash in SQLite, not as plaintext. Change it immediately after first login in production.

```env
ADMIN_USERNAME=admin
ADMIN_DEFAULT_PASSWORD=replace-with-a-long-random-password
ADMIN_COOKIE_SECURE=false
```

## Provider onboarding (minimal)

1. Login to Web Studio at `http://localhost:9890/`
2. Navigate to "Providers" (服务商)
3. Click "Create Provider"
4. Select `openai_image` provider type
5. Fill: name, base_url, default_model, api_key
6. Enable the provider
7. Navigate to "Generate Image" (生成图片)
8. Select your custom provider from dropdown
9. Optionally fill `provider_model` with the upstream model name for this request, or leave it blank to use the custom provider default
10. Enter prompt and submit

`provider_model` is a custom-provider upstream model override. It is not a local catalog model id and is only accepted when `model` is `custom:<provider_id>`.

Built-in, catalog, and reserved providers are not editable from the v0.2.0 Studio. Their keys, base URLs, and enablement are controlled by environment/runtime configuration, and Studio shows them as read-only compact summaries. Pollinations is experimental, disabled by default, and excluded from the default usable chain unless explicitly enabled and selected.

## Jobs and Assets

- **Jobs:** View list of generation jobs with status, type, duration
- **Assets:** View list of generated/uploaded assets with thumbnails

Both are minimal usable list/visibility surfaces in v0.2.0. Generated image/video assets and uploaded assets are visible, but advanced job detail, event timelines, diagnostics, and full asset management are planned for later releases.

## Security notes

- **Admin API:** Uses HttpOnly session cookie authentication
- **API mode API Keys:** Used for `/v1/images/generations`, `/v1/videos`, `/v1/jobs`, and protected media endpoints
- **API Key boundary:** Gateway API Keys cannot access admin account APIs such as `/v1/admin/account`, `/v1/admin/username`, or `/v1/admin/password`
- **File access:** `/generated/*` and `/uploads/*` require authentication and support authenticated `HEAD`
- **Health endpoint:** `/health` returns minimal `{"status":"ok"}` (no secrets)
- **Request hash:** Generation requests include dedupe/admission via request_hash
- **Secrets protection:** Provider secrets, raw URLs, and sensitive data are not exposed in Web Studio summaries

## Current limitations (v0.2.0)

The following features are not yet implemented:

- Full visual provider chain configuration UI
- AI Assistant (WIP / disabled — backend exists but no public routes, not part of v0.2.0 stable)
- Background worker, Celery, Redis, Kubernetes deployment primitives, and job event timeline UI
- Multi-user, tenant, SaaS, billing, and quota products
- Old admin restoration
- Automatic legacy data import/backfill
- Google provider support
- Complete diagnostics UI
- Runtime routing is not yet fully catalog-driven (catalog YAML exists and is exposed via API, while `routing.py` still owns the default image chain)
- Video ref_inputs upload (catalog declares ref_inputs; the Generate Video page shows them as read-only planned fields)
- React/Vue/npm build tooling is not part of this repository; Web Studio ships as static browser assets

## Legacy v0.1.0 reference

v0.1.0 had a broader legacy UI and more old user-facing controls. It remains useful as a reference for users who specifically need the old local-only experience, but it does not share the v0.2.0 security boundaries and Web Studio architecture.

For new deployments, v0.2.0 is the recommended baseline. Use any legacy v0.1.0 README or release archive only with the security trade-offs understood. The `v0.1.0` tag is available in the repository for historical reference.

## Version

AngeMedia Gateway v0.2.0 - Core-Safe + Minimal Web Studio + Minimal Provider Onboarding + Catalog-Aware Generate Video

The previous experimental name was Image Proxy Gateway. There is no compatibility promise for the old name because the project had no public users yet.

v0.2.0 focuses on safe foundation, minimal usable Web Studio, catalog-aware Generate Image/Generate Video surfaces, and authenticated local media delivery. v0.2.x may recover more user-facing experience, but unsupported features above are not part of this release contract.

## License

MIT

## Modular backend layout

The compatibility entry remains:

```text
scripts/proxy.py
```

The real backend implementation lives in:

```text
scripts/angemedia_gateway/
```

This splits configuration, configuration metadata, SQLite state, schemas, media localization, routing, assistant logic, providers, and FastAPI route assembly. `server.py` only assembles the app; page, admin, media, and storage routes live under `scripts/angemedia_gateway/routes/`.

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

## Security note: GATEWAY_API_KEY

When `GATEWAY_API_KEY` is empty, AngeMedia allows local/LAN clients to call image and video generation APIs without an API key. This is intentional for self-hosted local deployments.

For public deployments, always configure `GATEWAY_API_KEY`, use HTTPS reverse proxy protection, and change the default admin password.

- `UPLOAD_MAX_FILES` controls how many files `/v1/uploads` accepts in one request; default is 10. Remote media localization validates the original URL and every redirect target to reduce SSRF risk.

## Docker image publishing

The DockerHub workflow no longer runs on ordinary `main` pushes. Pull requests build without pushing when Docker-relevant files change. `v*` tags publish the matching version tag, and stable tags without a prerelease suffix also publish `latest`. A manual run builds only by default; explicitly enabling its `push_image` input publishes the `edge` tag.

Configure these repository settings before relying on image publishing:

- Secrets: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`
- Variable: `DOCKERHUB_REPOSITORY` with the full DockerHub image name, for example `dockerhub-user/angemedia-gateway`

The Dockerfile copies the `app/` directory, so Studio and related pages are available in container deployments.
