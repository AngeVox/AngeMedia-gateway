# Changelog

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
