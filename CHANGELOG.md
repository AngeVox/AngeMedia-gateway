# Changelog

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
