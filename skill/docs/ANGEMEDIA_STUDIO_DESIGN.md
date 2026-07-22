# AngeMedia Studio 设计说明

> Archived / historical design reference, not current v0.2.11 release contract.

AngeMedia Studio 是 v0.1.0 内置的轻量 Web UI。

## 设计目标

- 能快速测试图片、视频、路由、提示词增强；
- 能查看 provider 状态；
- 能预览本地化后的 `/generated/` 结果；
- 风格上与 AngeVoice 属于同一系列，但不照搬。

## 与 AngeVoice 的关系

借鉴点：

- Ange 系列字标；
- 柔和渐变背景；
- 玻璃拟态面板；
- 顶部状态栏；
- 状态胶囊；
- light/dark 主题；
- Toast 提示。

差异点：

- AngeMedia 是媒体生成工具，结果预览区域更大；
- 工作台以图片/视频输出为中心；
- 保留路由建议和提示词增强按钮；
- 增加渠道状态和接入说明页签。

## 后续建议

v0.1.0 的前端只是轻量 Studio。后续可以继续补：

- 生成历史数据库；
- 视频任务队列；
- Provider 配置管理；
- Prompt 增强前后对比；
- 多图上传和角色标注；
- 更精致的 Ange 系列视觉系统。
