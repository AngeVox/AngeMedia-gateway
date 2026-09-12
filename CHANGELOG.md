# Changelog

## [v0.2.13] - 2026-09-12

### EN

#### Added

- Added a brokerless Local Queue backend for single-node deployments. Fresh fnOS installs now use Local Queue by default, while Redis/Celery remains an optional advanced backend for higher concurrency and existing deployments.
- Added explicit Provider transport policy with `direct` and `explicit_proxy` modes, global defaults, per-provider overrides, and strict `trust_env=False` behavior so ambient proxy environment variables never capture provider credentials.
- Added admin-authorized Provider endpoint policy for localhost, RFC1918/CGNAT/ULA, split-DNS, and public relay endpoints while continuing to reject malformed URLs, metadata services, link-local, multicast, unspecified, and unsupported special-use targets.
- Added Provider-aware Assistant diagnostics that consume the same endpoint and transport decisions as generation and connection tests instead of maintaining a separate private-network policy.
- Added unified reference-image delivery for multipart, data URL, bare Base64, public URL, and relay-required providers.
- Added an optional External HTTP Reference Relay backend so URL-only providers can consume gateway-owned uploads/assets without requiring users to find their own public image host.
- Added Studio controls for global/per-provider transport and reference relay configuration. Sensitive proxy/relay values are write-only and never echoed back into the browser.

#### Changed

- Fresh fnOS installs no longer depend on the fnOS Redis application or host port 6379. Existing upgrades preserve their current queue backend and do not silently switch Redis/Celery installations to Local Queue.
- Provider connection tests, custom generation, status/quota probes, and runtime generation now share the same Provider transport resolver.
- URL-only image/video models can accept local uploads or existing gateway assets in Studio; AngeMedia performs relay delivery when configured, while public URL fields remain available as an advanced fallback.
- Studio diagnostics now describe Local Queue as local execution without a Broker instead of reporting Redis as disconnected.
- Custom Provider IDs that collide with builtin/catalog Provider IDs are rejected to keep endpoint and transport namespaces unambiguous.

#### Fixed

- Removed obsolete Provider admin save/test paths, the legacy Provider URL policy shim, an unused public-URL wrapper, and unused Assistant/Transport/Reference convenience APIs that duplicated current authority paths.
- Removed the stale Studio assumption that localhost/private Provider endpoints are always SSRF failures; strict SSRF validation remains unchanged for user-supplied media downloads.
- Clearing or deleting a custom Provider now also clears its per-provider transport override so a later Provider with the same ID cannot inherit a stale proxy configuration.

#### Security

- Provider proxy URLs, proxy credentials, relay upload URLs, and relay tokens are never returned by Admin read APIs or Studio.
- Reference Relay only accepts gateway-owned image assets or validated image data URLs, uses bounded image sizes and MIME checks, validates returned public URLs, and keeps `trust_env=False`.
- Custom provider status summaries no longer contain any secret-capable branch; Studio-safe `api_key_configured` remains a derived boolean only.

### ZH

#### 新增

- 新增无 Broker 的 Local Queue，面向单机/家庭 NAS 场景。fnOS 新安装默认使用 Local Queue；Redis/Celery 保留为高并发和既有部署可选的高级后端。
- 新增 Provider Transport Policy，支持全局与单 Provider 的 `direct` / `explicit_proxy`，并始终保持 `trust_env=False`，避免环境代理在未授权情况下接管 Provider 凭据流量。
- 新增管理员授权的 Provider Endpoint Policy：允许 localhost、RFC1918/CGNAT/ULA、split DNS 与公网中转地址；仍拒绝非法 URL、metadata service、link-local、multicast、unspecified 与不支持的 special-use 地址。
- 小助手网络诊断改为复用与生成、连接测试相同的 Provider endpoint / transport 决策，不再维护独立的私网阻断规则。
- 新增统一参考图交付层，覆盖 multipart、data URL、裸 Base64、public URL 与 relay-required Provider。
- 新增可选 External HTTP Reference Relay，使只接受公网 URL 的 Provider 也能消费 AngeMedia 自有上传/资产，不再要求用户自行寻找图床。
- Studio 新增全局/单 Provider 连接方式与 Reference Relay 配置；代理和 Relay 的敏感地址/凭据均只写不读，不会回显到浏览器。

#### 变更

- fnOS 新安装不再依赖 fnOS Redis 套件或宿主机 6379 端口；升级保留当前 Queue backend，不会静默把既有 Redis/Celery 切换为 Local Queue。
- Provider 连接测试、自定义生成、status/quota probe 与实际生成统一使用同一 Transport Resolver。
- URL-only 图片/视频模型在 Studio 中也可以直接上传本地图或选择已有资产；配置 Relay 后由 AngeMedia 自动中转，公网 URL 仍作为高级备用输入保留。
- Studio 诊断对 Local Queue 显示“本地执行 / 无需 Broker”，不再误报 Redis 未连接。
- 禁止创建与 builtin/catalog 同名的 Custom Provider，避免 endpoint/transport namespace 冲突。

#### 修复

- 清理已被新体系替代的 Provider admin save/test 旧路径、Provider URL compatibility shim、未使用 public-URL wrapper，以及 Assistant/Transport/Reference 中只形成第二套表达的未使用便利接口。
- 移除 Studio 中“localhost/私网 Provider endpoint 一律属于 SSRF 错误”的旧假设；用户提供的媒体下载 URL 仍维持严格 SSRF 防护。
- 删除 Custom Provider 时同步清理其 per-provider transport override，避免以后复用同 ID 时静默继承旧代理。

#### 安全

- Provider proxy URL/凭据、Relay upload URL/token 不会通过 Admin 读取 API 或 Studio 回显。
- Reference Relay 仅处理网关自有图片资产或已验证 image data URL，限制大小与 MIME，并再次校验 Relay 返回的公网 URL，同时保持 `trust_env=False`。
- Custom Provider 状态汇总移除任何可返回 secret 的分支；Studio 的 `api_key_configured` 始终只是派生布尔值。

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
