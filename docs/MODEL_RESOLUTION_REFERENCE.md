# 各渠道模型尺寸参考

> 下表列出常用规格。选择前请核对 Studio 或目录 API 返回的型号能力。

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

Sunburst、Flare与`gpt-image-2`当前开放基础单图生成/编辑，最多16张参考图和1份独立mask；掩码须符合官方格式、原图尺寸和透明通道要求。型号质量上限仍分别校验，目录声明不证明账户权限或实际生成效果。

## 4. BytePlus Seedream 5

| 模型 | 当前尺寸合同 |
|---|---|
| `seedream-5-pro` | freeform；总像素 `921600–4624220`，最大比例 16:1；预设含 `1024x1024`、`2048x2048`、`2816x1584`、`1584x2816` |
| `seedream-5-flash` | 与Pro共用单图尺寸合同，命名档位 `1K` / `1.5K` / `2K`；像素与比例边界相同，目录为唯一校验来源 |
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

### Video 2.5 与 2.5 Flash

| 参数 | 当前合同 |
|---|---|
| `seconds` | 字符串 `4–12`，默认 `5` |
| `size` | 2.5 可选 `720P` / `1080P` / `1K` / `2K`，2.5 Flash 使用 `720P` |
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
| 视频（显式选择） | `agnes-video-2.5` 或 `agnes-video-2.5-flash` | `720P`、`5s`、`16:9` |
| 旧帧数视频 | `agnes-video-v2.0` | `1152x768`、`121` 帧 |


## 政策审核与实际账户证据

目录的provider和model均可附带policy。没有审核记录时，API返回review_status=unknown；这不等于平台不可用。已审核记录包含公开sources、reviewed_at和review_after，后者是本项目的下次复核日期，不是平台承诺的政策结束时间。

- review_status为current时，billing可表示paid、promotional、usage_credits或unknown；promotional表示阶段性优惠，不表示永久免费。
- 到达review_after或系统日期早于reviewed_at时，状态变为review_due，当前billing回落unknown；reviewed_billing保留当时的结论供追溯。
- lifecycle区分documented、legacy_unverified、retired和unknown。未列入一次目录不足以认定退役；已经记录的retired不会因为审核到期重新激活。
- account_requirements只描述使用前提，evidence_scope始终为public_documentation；account_access_verified和generation_verified均为false。它们不会替代连接观察、账户检查或真实任务结果。

管理接口的agnes_video.default_model与VideoRequest和路由预览使用同一请求默认值，不按目录排列或促销型号自动换默认。default_model_policy用于说明这个默认值的政策状态，不是可用性背书。请求方可显式选择已核实权限的型号。

## 渠道输入和任务处理

### OpenAI Image

使用 `output_format` 选择 PNG、JPEG 或 WebP。`output_compression` 用于 JPEG 或 WebP，取值 0–100。透明背景需要 PNG 或 WebP。`response_format` 控制网关如何返回结果，Studio 的队列任务通过作品地址交付。自定义兼容渠道可用的输入以其能力声明为准。

### SiliconFlow

显式型号 `siliconflow-qwen-image` 使用 `Qwen/Qwen-Image` 文生图，`guidance` 映射为渠道的 `cfg`。参考图输入请选择目录中支持该操作的型号。SiliconFlow 请求已停用 `batch_size`。目录外型号使用 `siliconflow::上游ID` 显式选择，网关只发送已支持的通用文字生成字段。

### Pollinations

生成和编辑需要渠道 API 密钥。P-Image Edit 支持最多 5 张参考图。若收到 402，请检查 Pollen 余额、密钥预算和该型号的计费方式。

### ModelScope

Studio 从账户目录中筛选已适配的模型，并按所选模型显示生成或编辑输入。图片编辑把参考图交给上游的 `image_url` 字段，单次提交的查询和保存都沿用同一渠道任务号。目录、实际可选参数与型号对应。账户模型权限、魔粒余额和本地提交预算请分别核对。

明确拒绝时，网关可继续用户已批准的候选链。提交超时、没有确认任务号或已取得任务号时，网关保留原任务。后续查询和保存只围绕原提交进行。端点、代理或凭据变化后，先恢复对应渠道配置，再查询原任务。

### 渠道状态和确认

`GET /v1/admin/catalog` 提供管理员可见的渠道启用状态和密钥配置状态。两者只描述本地配置。先在渠道页验证连接和权限，再通过实际生成确认结果。

默认图片链按操作、参数和渠道配置筛选，保留可用候选的顺序。显式选择的型号会按原型号校验。助手在生成前会显示所选渠道、型号和参数。候选或配置在确认前发生变化时，请重新检查并确认方案。
