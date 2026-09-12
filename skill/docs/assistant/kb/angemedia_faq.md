# AngeMedia Local Knowledge Base

## What AngeMedia Gateway Does

AngeMedia Gateway is a self-hosted media generation gateway and Web Studio. It manages image and video generation through queued jobs, safe provider/channel configuration, Jobs, Dashboard, Assets, and controlled local media paths.

## Main Studio Pages

- Dashboard shows queue status, recent jobs, recent failures, recent assets, and storage summary.
- Generate Image submits queued image jobs and previews linked generated assets.
- Generate Video submits queued video jobs, including text-to-video and image-to-video when the selected channel supports references.
- Jobs shows server-side paginated task status, events, attempts, diagnostics, and linked assets.
- Assets shows generated or uploaded media with safe job/generation/channel/model summaries.
- Channels stores built-in and custom channel configuration. API keys are write-only and should never be displayed back.
- Diagnostics shows safe runtime, queue, database, media, and recent failure summaries.

## Prompt and Language Rules

The Chinese interface may show Chinese explanations, but generation prompts sent to media models should be English. English users should not be forced into Chinese output.

## Safe Paths

The UI should only display controlled `/generated` and `/uploads` media paths. It must not display local filesystem paths, signed provider URLs, raw provider responses, request hashes, raw input JSON, raw output JSON, API keys, or Authorization headers.

## Queue and Jobs

Jobs are the source of truth for queued generation. The queue path is HTTP submission, SQLite jobs/job_dispatches, dispatcher, Celery/Redis broker, worker runtime, stage handler, job_events/job_attempts, and finally Assets.

## Timeouts

Image and video generation timeouts should be global settings, not copied into every channel. Video generation can take much longer than image generation; a video timeout of at least 900 seconds is a reasonable default.

## LLM Assistant Settings

The top navigation Assistant entry opens the shared AngeMedia assistant. Its Settings button configures the same LLM used by the main assistant and Prompt Copilot. Enter an OpenAI-compatible base URL, API key, and model. Fetch Models and Test Connection should use the unsaved form values, so users do not need to save first. Saving with an empty API key keeps the existing stored key unless the UI provides an explicit clear action.

For Chinese users, explain LLM settings as: 顶栏“小助手” → 设置 → 填写 API 调用地址、API Key、模型 → 获取模型或测试连接 → 保存设置。Do not suggest unrelated model names that are not present in the user's configured model list.

## Agnes Image Sizes

Agnes Image 2.1 supports the released image presets: 1024x768, 1024x1024, 768x1024, 1280x720, 720x1280, 1536x1024, 1024x1536, and 4096x4096. The model supports text-to-image and image-to-image. Image-to-image uses safe `/generated` or `/uploads` references and sends only the provider-required encoded reference internally.

Agnes Video v2.0 supports text-to-video and image-to-video through queued jobs. The released video presets are 1152x768, 768x1152, and 2048x1536. Video jobs may take several minutes and should be diagnosed through Jobs detail, Dashboard, and Diagnostics.

## Common Diagnostics

- `not_configured`: the channel API key or base URL is missing.
- `timeout`: the upstream channel did not complete inside the configured timeout.
- `ambiguous_submit`: the upstream submit response did not clearly contain a task id, so automatic resubmit is unsafe.
- `provider_response`: the upstream channel returned an error response.

## Channel Expansion Policy

New video channels such as Runway, Kling, Vidu, MiniMax, or Google should be added one adapter at a time. A selectable channel requires configuration schema, capability catalog, mock contract tests, error mapping, safe local asset import, and Jobs/Assets summaries.

## Assistant Scope

The assistant can answer AngeMedia usage, channel configuration, queue/job diagnostics, prompt planning, asset flow, supported size, timeout, and integration questions. It should refuse unrelated general-purpose questions.
