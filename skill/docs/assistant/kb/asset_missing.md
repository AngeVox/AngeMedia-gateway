# Asset Missing Runbook

任务显示 succeeded 但 Assets 没有结果时，先看 Job 的 asset_count、受控 asset path、generation summary，以及当前 stage 是否真正完成 asset import/finalize。Provider 返回“完成”不等于本地资产已经成功保存。

视频常见链路是 Provider completed → asset_import → finalize。若失败发生在 asset_import，应检查 result URL 是否存在、URL 是否被安全策略拒绝、下载是否超时或本地存储是否可写。图片也应确认生成结果已被本地化或登记到 Assets。

不要直接把 Provider 签名 URL 当永久结果展示。Studio 应优先使用受控 `/generated` 或 `/uploads` 路径，原始本地文件路径和签名 URL 不应暴露给 Assistant。