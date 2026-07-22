# Agnes 模型调用索引

> 本页是 AngeMedia v0.2.11 的 Agnes 调用索引。具体字段以网关当前 adapter、catalog 和测试为准，不把尚未接入的上游能力写成可用合同。

## 文档索引

- `docs/AGNES_IMAGE_CALL_EXAMPLES.md`
  - Agnes Image 2.1 命名尺寸档位与宽高比
  - Agnes Image 2.0 自由尺寸与 `seed`
  - 单图和最多 4 张多图参考
  - URL / `b64_json` 返回
  - 严格参数边界

- `docs/AGNES_VIDEO_CALL_EXAMPLES.md`
  - 文生视频、图生视频和关键帧风格提交
  - 异步任务与 Web Studio Jobs / Assets
  - 当前任务查询端点与旧端点受限回退
  - 完成响应中的 `metadata.url`
  - HTTP 503 且无任务 ID 时不自动重提

## v0.2.11 已验证适配

- Agnes Image 2.1：`1K` / `2K` / `3K` / `4K`，以及 `1:1`、`3:4`、`4:3`、`16:9`、`9:16`、`2:3`、`3:2`、`21:9`。
- Agnes Image 2.0：受 catalog 边界约束的 `WIDTHxHEIGHT` 与 `seed`。
- Agnes 图片参考输入：1～4 张安全图片，由网关物化后提交。
- Agnes Video：优先使用当前任务查询端点，并兼容完成响应中的 `metadata.url`。
- 视频提交返回 HTTP 503 且没有任务 ID 时，提示上游繁忙并禁止自动重提。

Agnes 文档入口：`https://agnes-ai.com/zh-Hans/docs/overview`。接口变化时，不能只改文档或只改 adapter；应同时更新 catalog、前端能力投影、测试和发布包。
