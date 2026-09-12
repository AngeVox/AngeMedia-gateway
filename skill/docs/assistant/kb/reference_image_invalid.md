# Reference Image Invalid Runbook

参考图失败时先区分“模型不支持参考图”和“参考输入本身不符合渠道合同”。使用 catalog_model_capabilities 查看目标模型的 image_to_image / image_edit 能力、reference roles、最大数量和格式要求，不要只根据模型名称猜测。

持久化 Job 应优先保存 AngeMedia 自己可控的媒体引用，不保存短期签名 URL 或大段 data URL。某些 Provider 接受公共 URL，某些需要编码数据，某些只接受 URL；这些转换属于 Provider adapter，不应要求用户手工拼请求体。

如果安全策略拒绝 URL，不要关闭 SSRF 防护来绕过。应确认引用是受控本地资产、合法公共地址，或使用对应渠道明确支持的输入方式。