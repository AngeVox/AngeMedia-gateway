# Changelog

## [v0.2.12] - 2026-09-12

### EN

#### Added

- Added current provider/model support for OpenAI GPT Image 2.5 Sunburst and Flare, ModelScope Qwen Image Edit 2511, SiliconFlow Qwen Image Edit 2509, BytePlus Seedream 5.0 Pro/Lite, Pollinations image editing, Agnes Image 2.5 Flash, and Agnes Video 2.5.
- Added unified image edit inputs (`operation`, ordered `reference_images`, and `mask`) with catalog-driven Studio controls and safe multipart/data-URL handling.
- Added explicit capability declarations for custom OpenAI-compatible image providers; custom providers remain text-to-image only unless edit capability is declared.
- Added a read-only upstream model audit for OpenAI, SiliconFlow, and Pollinations. It reports drift for human review and never rewrites the production catalog automatically.

#### Fixed

- Fixed Agnes Video opaque `video_id` handling across submit, SQLite persistence, worker polling, manual refresh, asset import, and restart recovery without weakening path-segment validation.
- Fixed Agnes Video 2.5 polling to include `model_name=agnes-video-2.5` and added its current text/keyframe/reference request contract.
- Fixed OpenAI image Base64 results so successful generations can be safely localized into gateway assets.
- Removed the obsolete ModelScope submit task-type header while retaining the documented poll task type.
- Split local-fetch SSRF validation from provider-reference URL validation so transparent fake-IP DNS cannot break safe cross-provider references while local downloads remain strictly DNS/IP checked.
- Stopped Agnes Video from inheriting the Agnes Image base URL override; the shared Agnes API key can still be inherited independently.

#### Changed

- The default explicit OpenAI image alias now targets `gpt-image-2.5-sunburst`; `gpt-image-2` remains available as a compatibility model.
- The default Agnes image alias now targets `agnes-image-2.5-flash`; 2.1 and 2.0 remain selectable.
- Agnes Video v2.0 remains the stable default request contract; Agnes Video 2.5 is selectable explicitly and may require model-level account access.
- Image request hashing moved to v2 so edit operation, ordered references, mask identity, and current output controls participate in deduplication without persisting raw image data or signed URLs.
- Provider reference controls are now capability-driven: URL-only providers receive public URLs, while gateway-owned uploads/assets stay on providers that can safely consume materialized local data.
- ModelScope async image polling now defaults to a bounded 600 s window with a 5 s interval because live hosted tasks remained legitimately `RUNNING` well beyond the previous 120 s default.
- Redis/Celery packaging and runtime remain unchanged in v0.2.12; local queue decoupling is deferred to v0.2.13.

### ZH

#### 新增

- 同步当前 Provider/模型：OpenAI GPT Image 2.5 Sunburst/Flare、ModelScope Qwen Image Edit 2511、SiliconFlow Qwen Image Edit 2509、BytePlus Seedream 5.0 Pro/Lite、Pollinations 图片编辑、Agnes Image 2.5 Flash 与 Agnes Video 2.5。
- 新增统一图片编辑输入：`operation`、有序 `reference_images`、`mask`，Studio 按 catalog 能力动态渲染，并复用安全 multipart / data URL 处理。
- 自定义 OpenAI-compatible 图片渠道可显式声明编辑能力；未声明时仍保守限定为文生图。
- 新增只读上游模型审计，可检查 OpenAI、SiliconFlow、Pollinations 的模型漂移；只生成供人工审核的报告，不自动改生产 catalog。

#### 修复

- 修复 Agnes Video opaque `video_id` 在提交、SQLite、worker 轮询、手动刷新、资产导入和重启恢复链路中被旧 path-safe 正则误拒绝的问题，同时不放宽 URL path 的 `task_id` 校验。
- Agnes Video 2.5 轮询补充 `model_name=agnes-video-2.5`，并接入当前 text / keyframe / reference 请求合同。
- OpenAI 图片 Base64 返回结果现在可安全落盘并进入 Assets。
- ModelScope 提交阶段移除旧 task-type header，轮询仍保留官方 `image_generation` task type。
- 将本机下载 SSRF 校验与 Provider 参考图 URL 校验拆分，透明代理 Fake-IP 不再误伤安全跨 Provider 引用；AngeMedia 自己下载媒体时仍保持严格 DNS/IP 检查。
- Agnes Video 不再继承 Agnes Image 的 base URL override；共享 Agnes API key 仍可独立继承。

#### 变更

- `openai-image` 默认显式模型切到 `gpt-image-2.5-sunburst`，旧 `gpt-image-2` 继续保留兼容。
- `agnes-image` 默认切到 `agnes-image-2.5-flash`，2.1 / 2.0 继续可选。
- Agnes Video v2.0 保持稳定默认合同；Agnes Video 2.5 可显式选择，并可能需要账号具备模型级权限。
- 图片 request hash 升级到 v2，将编辑操作、有序参考图、mask identity 与当前输出控制纳入去重，同时不持久化原始图片数据或签名 URL。
- Provider 参考图控件改为 capability-driven：URL-only 渠道只接收公网 URL；能安全物化本地数据的渠道才显示 `/uploads` / `/generated` 资产入口。
- ModelScope 异步图片轮询默认改为 600 秒有界窗口、5 秒间隔，因为真实 hosted task 合法 `RUNNING` 时长已明显超过旧 120 秒默认值。
- v0.2.12 不改 Redis/Celery 打包与运行方式；本地队列解耦继续放在 v0.2.13。

## [v0.2.11] - 2026-07-22

### EN

#### Fixed

- Updated Agnes Video polling to use the current task endpoint with a bounded legacy fallback.
- Accepted the current completed-video response at `metadata.url` and separated missing result URLs from unsafe URLs.
- Added clear non-retrying guidance for submit-time HTTP 503 responses without a task ID.
- Made fnOS/FYGO package settings actually reset administrator credentials, revoke sessions, and back up the SQLite database first.

#### Changed

- Updated Agnes Image 2.1 for named size tiers and aspect-ratio parameters.
- Built one offline fnOS/FYGO package for both x86_64 and ARM64.
- DockerHub release images now publish one multi-architecture manifest for `linux/amd64` and `linux/arm64`.
- Aligned runtime, Docker Compose, skill, API docs, and package metadata to v0.2.11.

### ZH

#### 修复

- Agnes Video 轮询改用当前任务查询端点，并保留受限的旧端点兼容回退。
- 兼容完成响应中的 `metadata.url`，并将“结果缺少 URL”与“不安全 URL”分开诊断。
- 视频提交返回 HTTP 503 且没有任务 ID 时给出清晰提示，同时保持不自动重提。
- fnOS/FYGO 应用设置现在会真实重置管理员凭据、撤销旧会话，并在修改前备份 SQLite 数据库。

#### 变更

- Agnes Image 2.1 适配命名尺寸档位和宽高比参数。
- fnOS/FYGO 离线包统一支持 x86_64 与 ARM64。
- DockerHub 发布镜像改为同时包含 `linux/amd64` 与 `linux/arm64` 的多架构清单。
- 运行时、Docker Compose、Skill、API 文档和包元数据统一更新到 v0.2.11。

## [v0.2.1] - 2026-07-03

### EN

#### Added

- Web Studio coverage for image generation, video generation, jobs, assets, channels, diagnostics, API keys, and assistant settings.
- Queue-first image and video generation with dispatcher and worker processes.
- Agnes Video v2.0 as the primary released video path.
- Protected local media import for generated and uploaded assets.
- Public README, Chinese README, and release notes for external users.

#### Changed

- Documented SiliconFlow/Kolors as the stable image-to-image path.
- Kept Agnes Image documented as an explicit image channel rather than a default stable image-to-image promise.
- Synced agent skill documentation with the public docs source.
- Tightened release packaging hygiene to block only named internal documents.

#### Security

- Release hygiene excludes known internal audit, handoff, release-report, development, design QA, and assistant execution-plan files.
- Documentation now consistently states that `/generated/*` and `/uploads/*` are protected media paths.
- Hardened job and error sanitization against data URL ReDoS by replacing regex-based data URL redaction with bounded linear scanning.
- Removed an insecure temporary-file test fixture reported by code scanning.

### ZH

#### 新增

- Web Studio 覆盖图片生成、视频生成、任务、资产、渠道、诊断、API 密钥和小助手设置。
- 图片和视频生成走队列优先架构，包含 dispatcher 和 worker 进程。
- Agnes Video v2.0 是当前发布版主视频路径。
- 生成和上传资产会导入到受保护的本地媒体路径。
- 为外部用户补齐英文 README、中文 README 和版本记录。

#### 变更

- 明确 SiliconFlow/Kolors 是稳定图生图路径。
- 将 Agnes Image 定位为显式图片渠道，而不是默认稳定图生图承诺。
- 同步 Agent skill 文档与公开 docs 源文档。
- 收紧发布打包卫生检查，只拦截明确命名的内部文档。

#### 安全

- 发布卫生检查会排除已知内部审计、交接、发布报告、开发、设计 QA 和小助手执行计划文件。
- 文档统一说明 `/generated/*` 和 `/uploads/*` 是受保护媒体路径。
- 将基于正则的 data URL 脱敏替换为有长度上限的线性扫描，增强 job 和 error 脱敏以防 data URL ReDoS。
- 移除 Code Scanning 报告的不安全临时文件测试 fixture。
