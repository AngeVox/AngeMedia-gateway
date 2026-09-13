# 各渠道模型尺寸参考

> v0.2.13 以当前 `providers/catalog/models.yaml` 为真相源。Studio 和请求校验都应读取 catalog，不应在 Agent 文档中另造一套尺寸规则。

## 1. 默认图片链

默认链仍为：

```text
kolors → qwen → flux → z-image → z-turbo
```

| 别名 | 当前尺寸合同 |
|---|---|
| `kolors` | 固定 catalog 预设；默认 `1024x1024` |
| `qwen` | `1024x1024`、`1328x1328`、`1664x928`、`928x1664`、`1472x1104`、`1104x1472`、`1584x1056`、`1056x1584` |
| `flux` | 当前已验证 `1024x1024` |
| `z-image` | freeform；边长 `64–2048`，总像素 `262144–4194304`，常用 `1024x1024` / `1280x720` / `720x1280` |
| `z-turbo` | 当前已验证 `1024x1024` |

ModelScope adapter 会把 catalog 允许的 `size` 发送给上游；不要再使用旧文档里的“不透传 size”假设。

## 2. Agnes Image

当前推荐：`agnes-image` / `agnes-2.5` → `agnes-image-2.5-flash`。

| 模型 | 尺寸合同 |
|---|---|
| Image 2.5 / 2.1 | 命名档位 `1K/2K/3K/4K`；自由尺寸边长 `512–4096`，像素总量 `262144–16777216`；支持 catalog 列出的 legacy 预设 |
| Image 2.0 | 边长 `512–2048`，像素总量 `262144–3145728` |

Image 2.5 / 2.1 还支持 `aspect_ratio`：`1:1`、`3:4`、`4:3`、`16:9`、`9:16`、`2:3`、`3:2`、`21:9`。

## 3. OpenAI Image

当前显式默认：`openai-image` → `gpt-image-2.5-sunburst`。

Sunburst、Flare 和兼容 `gpt-image-2` 的当前 catalog 尺寸边界：

- 边长必须是 16 的倍数；
- 单边最大 3840；
- 总像素 `655360–8294400`；
- 最长边 / 最短边不超过 3:1；
- 常用预设：`1024x1024`、`1536x1024`、`1024x1536`。

Sunburst / `gpt-image-2` 当前开放编辑；Flare 在 v0.2.13 只开放已无冲突证据的生成能力。

## 4. BytePlus Seedream 5

| 模型 | 当前尺寸合同 |
|---|---|
| `seedream-5-pro` | freeform；总像素 `921600–4624220`，最大比例 16:1；预设含 `1024x1024`、`2048x2048`、`2816x1584`、`1584x2816` |
| `seedream` / `seedream-5-lite` | freeform；总像素 `3686400–16777216`，最大比例 16:1；预设含 `2048x2048`、`3072x3072`、`4096x4096` 等 |

两者都是显式渠道，不进默认链。上游最终需要公开 URL；安全公网 URL 可直接使用，网关自有本地图片或安全 data URL 在配置 Reference Relay 后可自动中转。

## 5. Pollinations

Pollinations 模型池是动态的。v0.2.13 静态 catalog 只保留稳定 alias：

- `pollinations` → `zimage`
- `pollinations-edit` → `p-image-edit`

具体模型变化可运行：

```bash
python scripts/audit_upstream_models.py --provider pollinations
```

Audit 只报告差异，不自动修改 catalog。

## 6. Agnes Video

### 当前 Video 2.5

| 参数 | 当前合同 |
|---|---|
| `seconds` | 字符串 `4–12`，默认 `5` |
| `size` | `720P` / `1080P` / `1K` / `2K` |
| `aspect_ratio` | `21:9` / `16:9` / `4:3` / `1:1` / `3:4` / `9:16` |
| `mode` | `text` / `keyframe` / `reference` |

2.5 不使用 `width/height/num_frames/frame_rate`。其 keyframe/reference 上游最终需要公开 URL；安全公网 URL 可直接使用，本地网关资产需要已配置的 Reference Relay。

### Video v2.0 兼容

旧模型继续保留 `1152x768`、`768x1152`、`2048x1536` 等 catalog 预设以及 `81/121/161/241/441` 帧合同。只在旧调用或受保护本地参考资产兼容场景使用。

## Agent 快速选择

| 场景 | 推荐模型/渠道 | 推荐尺寸 |
|---|---|---|
| 日常通用图 | 默认链 / `kolors` | `1024x1024` |
| 中文海报 | `qwen` | catalog 预设 |
| 写实人像 | `z-turbo` | `1024x1024` |
| Agnes 图片 | `agnes-2.5` | `2K` + 合适 `aspect_ratio` |
| OpenAI 高质量/编辑 | `openai-image` | `1024x1024` 起 |
| Seedream 高质量 | `seedream-5-pro` | catalog 预设或合法 freeform |
| 视频默认 | `agnes-video-2.5` | `720P`、`5s`、`16:9` |
| 旧帧数视频 | `agnes-video-v2.0` | `1152x768`、`121` 帧 |
