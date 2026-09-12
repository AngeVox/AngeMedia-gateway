# Model Capability Mismatch Runbook

当请求被提示 model capability mismatch、unsupported operation、参考图/mask 不支持时，先使用 catalog_model_capabilities 查看模型声明，而不是尝试把其他模型的参数强塞给当前 Provider。

图片模型需要区分 text_to_image、image_to_image、image_edit；视频模型需要区分 text_to_video、image_to_video，并检查具体 operation 的 reference roles、数量和参数。可选择模型不代表所有操作都支持。

Studio 应按 catalog 隐藏或禁用不支持的输入项。若 Provider 实际能力发生变化，应更新 catalog/adapter 和合同测试，而不是在 Assistant 中增加特殊硬编码。