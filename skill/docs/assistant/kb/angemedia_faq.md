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

Jobs are the source of truth for queued generation. The queue path is HTTP submission, SQLite jobs/job_dispatches, dispatcher, the configured queue backend, worker runtime, stage handler, job_events/job_attempts, and finally Assets. Diagnose the active backend through the safe Diagnostics/queue summary instead of assuming a specific broker.

## Timeouts

Image and video generation timeouts should be global settings, not copied into every channel. Video generation can take much longer than image generation; a video timeout of at least 900 seconds is a reasonable default.

## LLM Assistant Settings

The top navigation Assistant entry opens the account-scoped AngeMedia assistant. Its Settings button configures the same LLM used by the main assistant and Prompt Copilot. Enter an OpenAI-compatible base URL, API key, and model. Fetch Models and Test Connection should use the unsaved form values, so users do not need to save first. Saving with an empty API key keeps the existing stored key unless the UI provides an explicit clear action.

For Chinese users, explain LLM settings as: 顶栏“小助手” → 设置 → 填写 API 调用地址、API Key、模型 → 获取模型或测试连接 → 保存设置。Do not suggest unrelated model names that are not present in the user's configured model list.

## Agnes Image and Video

Agnes Image 2.5 Flash is the current default Agnes image model. It supports text-to-image and image-to-image, freeform dimensions within the catalog limits, the released legacy presets 1024x768, 1024x1024, 768x1024, 1280x720, 720x1280, 1536x1024, 1024x1536, and 4096x4096, plus the catalog aspect ratios. Image-to-image supports up to four declared reference images. Agnes Image 2.1 and 2.0 remain compatibility models.

Agnes Video v2.0 is the compatibility default with current availability unverified and supports text-to-video and image-to-video through queued jobs. Its released presets are 1152x768, 768x1152, and 2048x1536. Agnes Video 2.5 is selectable for accounts with model permission and supports text, keyframe, and reference modes, 4-12 second duration, 720P/1080P/1K/2K size choices, and up to eight declared URL references. Video jobs may take several minutes and should be diagnosed through Jobs detail, Dashboard, and Diagnostics.

## Common Diagnostics

- `not_configured`: the channel API key or base URL is missing.
- `timeout`: the upstream channel did not complete inside the configured timeout.
- `ambiguous_submit`: the upstream submit response did not clearly contain a task id, so automatic resubmit is unsafe.
- `provider_response`: the upstream channel returned an error response.

## Channel Expansion Policy

New video channels such as Runway, Kling, Vidu, MiniMax, or Google should be added one adapter at a time. A selectable channel requires configuration schema, capability catalog, mock contract tests, error mapping, safe local asset import, and Jobs/Assets summaries.

## Assistant Scope

The assistant can answer AngeMedia usage, channel configuration, queue/job diagnostics, prompt planning, asset flow, supported size, timeout, and integration questions. It should understand natural creative requests and follow-ups from context, ask a short clarification when needed, and gently redirect clearly unrelated requests rather than requiring product keywords.

## 助手生成提案与人类确认

你可以直接说想做什么画面，再和助手一起调整。方案会放在对话下方，先看看完整描述、设置、使用的渠道和费用说明。确认无误并勾选需要的确认项后，选择“确认，开始生成”。只有保存方案还不会生成媒体，助手也不能替你点击确认。

修改后会产生新版本，旧勾选不再生效。响应丢失或刷新页面时只读取已保存状态，不自动重提。真实任务标识才代表已经入队；成功作品通过受控路径打开。目录声明、连接成功和本地模拟测试都不等于当前账户完成了真实生成。

“停止助手”停止的是对话运行，关闭对话或归档历史也不代表取消已入队的媒体任务。在任务页查看可用的取消和恢复操作。重新提交会发起新任务。

## 怎么理解进度

“消息已收到”表示助手收到了对话，不代表已经开始生成。确认方案后，排队、发送视频请求、等待服务方结果、取回并保存作品分别是不同阶段。“这轮答复已结束”只表示助手说完了，生成任务可能还在继续。只有查到这条任务对应的作品记录后，才会显示作品已保存并提供打开入口。

如果暂时查不到视频进度，会继续查看同一条任务，不会重新生成。等候超时也不一定表示服务方没有完成；先查看任务详情再决定下一步，避免重复提交。身份验证失败先检查渠道凭据，不需要因此反复改提示词。助手会用平常说话的方式解释这些情况，不要求你了解内部字段、工具名或错误码。


Agnes Video 2.5 Flash 支持 720P、最多 5 张参考图和 3 条参考音频。标准版 2.5 支持最多 8 张参考图、3 条参考音频和 1 段参考视频。音频和视频参考使用公开链接。渠道明确提示队列已满时，本次请求未受理，可以稍后重新发起。
