# 媒体模型路由参考（图片 + 视频）

> 这份文档给 Agent 做“先判断、再路由”使用。目标不是死记模型，而是让 Agent 能根据任务类型，按**能力矩阵 + 成本优先**做决策。

## 一、路由总原则

### 1. Cost-first

默认优先使用免费或低成本链路，不要一上来就走付费模型。

图片默认链：

```text
kolors → qwen → flux → z-image → z-turbo
```

`pollinations` 为实验性渠道，缺省关闭，不在默认降级链中。

视频现有型号：

```text
agnes-video-v2.0
agnes-video-2.5
```

`agnes-video-v2.0` 是当前代码中的新任务默认；当前服务状态仍需按账户实测核对，不能仅凭默认配置认定可用。`agnes-video-2.5` 需显式选择并具备对应模型权限。

### 2. 适配度优先于机械默认

如果任务特征非常明确，应直接选更适合的模型，而不是一律走默认链。

### 3. 用户显式指定优先

如果用户明确说：

- “用 Agnes 生图”
- “用 openai-image / GPT Image 2.5”
- “就用 qwen”

那应优先尊重用户指定，除非模型明显不支持该任务。

---

## 二、7 维能力矩阵

下面的 7 维，不是数学评分，而是给 Agent 做心智判断。

| 维度 | 要判断什么 |
|---|---|
| 1. 媒体类型 | 图片还是视频 |
| 2. 风格适配 | 写实、二次元、插画、概念、海报、产品图 |
| 3. 文字能力 | 是否要在图片里稳定渲染中文/标题/海报文案 |
| 4. 编辑能力 | 纯文生，还是图生图、重绘、多图参考 |
| 5. 运动能力 | 是否需要视频、关键帧、首尾帧过渡 |
| 6. 成本/可用性 | 能否先走默认免费链，是否需要显式付费 |
| 7. 输出约束 | 分辨率、比例、时长、帧数、是否返回 b64 |

---

## 三、图片路由规则

### 模型标识和渠道边界

图片请求的`model`接受目录内部ID（如`agnes-2-5`）、公开别名（如`agnes-2.5`）和目录中已登记的上游ID（如`Qwen/Qwen-Image-2512`）。这些标识都从同一份catalog解析。

目录外型号必须明确指定渠道，例如`modelscope::ExampleOrg/NewImageModel`或`siliconflow::ExampleOrg/NewImageModel`。这只是向指定渠道透传型号，不代表网关已验证该型号的能力、价格或账户权限；渠道运行开关仍生效。已知型号与显式渠道不匹配、视频型号用于图片、未知的无渠道标识都会返回400，不会静默落到ModelScope。

自定义渠道继续使用`model=custom:<渠道ID>`及可选的`provider_model`覆盖，不使用上述内置渠道范围。

### ModelScope本地保护预算

`MODELSCOPE_DAILY_LIMIT`表示网关本地每天可分配的提交尝试上限，默认50；它不表示平台魔粒、余额或免费调用次数。预算由共享SQLite按渠道和服务器本地日期记账。发出POST前占用一次，失败、超时或进程中断后不自动退回；轮询不占用新次数，上游429也不会清空本地预算。

升级时先停止仍写旧JSON计数的进程，保持迁移时原有日上限。新版本只在首次访问对应日期时导入旧计数，不修改旧文件；后续以SQLite为准。调整上限保留已分配数。先完成数据库和旧计数备份，再进行实际升级。

### 图片模型别名表

| 别名 | 渠道 | 实际模型 | 推荐场景 |
|---|---|---|---|
| `kolors` | SiliconFlow | `Kwai-Kolors/Kolors` | 默认主力通用图 |
| `qwen` | ModelScope | `Qwen/Qwen-Image-2512` | 中文海报、二次元、带字图片 |
| `flux` | ModelScope | `black-forest-labs/FLUX.1-Krea-dev` | 产品图、风景、自然光、摄影感场景 |
| `z-image` | ModelScope | `Tongyi-MAI/Z-Image` | 创意概念、超现实、艺术实验 |
| `z-turbo` | ModelScope | `Tongyi-MAI/Z-Image-Turbo` | 写实人像、商业摄影、快速出图 |
| `qwen-edit` / `qwen-image-edit` | ModelScope | `Qwen/Qwen-Image-Edit-2511` | 多参考图编辑，实验性显式模型 |
| `siliconflow-qwen-edit` | SiliconFlow | `Qwen/Qwen-Image-Edit-2509` | 最多 3 张参考图编辑，实验性显式模型 |
| `pollinations` | Pollinations | `zimage` 稳定 alias | 实验性，缺省关闭，不在默认降级链中 |
| `pollinations-edit` | Pollinations | `p-image-edit` alias | 图片编辑，实验性，缺省关闭 |
| `agnes-image` / `agnes-2.5` | Agnes AI | `agnes-image-2.5-flash` | 当前显式 Agnes 图片模型 |
| `agnes-2.1` / `agnes-2.0` | Agnes AI | 旧版 Flash | 兼容模型，显式调用 |
| `openai-image` | OpenAI | `gpt-image-2.5-sunburst` | 当前付费高质量生成/编辑，不进默认链 |
| `openai-flare` | OpenAI | `gpt-image-2.5-flare` | 当前快速生成/编辑；最多16参考图及可选mask；账户操作未据目录推定 |
| `gpt-image-2` | OpenAI | `gpt-image-2` | 兼容模型 |
| `seedream` / `seedream-5-lite` | BytePlus | `seedream-5-0-lite-260128` | 生成/多参考编辑，显式调用 |
| `seedream-5-pro` | BytePlus | `dola-seedream-5-0-pro-260628` | 高质量生成/多参考编辑，显式调用 |
| `seedream-5-flash` / `seedream-flash` | BytePlus | `dola-seedream-5-0-flash-260915` | 单图生成/最多10图参考编辑；支持1K/1.5K/2K，显式调用；账户权限未据目录推定 |

### A. 默认通用图

- 用户需求不特别明确
- 只是普通插图、场景图、配图

**做法**：可以省略 `model`，让网关走默认链。

### B. 中文海报 / 带字图片 / 二次元插画

优先：`qwen`

适用特征：

- 用户要求中文标题、slogan、按钮文案
- 偏二次元、国风插画、卡通海报
- 需要对中文指令理解更强

### C. 写实人像 / 商业摄影 / 美女写真

优先：`z-turbo`

适用特征：

- 真人感、摄影感、电影感
- 半身照、写真、模特图、商业肖像
- 用户强调“现实风格”“真人”“高级摄影”

### D. 产品氛围图 / 风景 / 自然光场景

优先：`flux`

适用特征：

- 商品主图、品牌氛围图
- 室内家居、风景、自然环境
- 柔和自然光、材质质感、构图干净

### E. 创意概念 / 超现实 / 脑洞图

优先：`z-image`

适用特征：

- 抽象概念、梦境、超现实视觉
- 画面创意性大于写实准确性

### F. 图生图 / 参考图

稳定路径：`kolors`（SiliconFlow/Kolors）

适用特征：

- 用户上传参考图并要求保留主体、构图或风格
- 用户要求把已有图片改成另一种视觉风格
- 需要一个 release 路径内更稳定的图生图选择

### G. 显式 Agnes 图片能力

当前优先：`agnes-image` / `agnes-2.5`；`agnes-2.1` / `agnes-2.0` 仅保留兼容。

适用特征：

- 用户明确要 Agnes
- 要做 Agnes 风格对比测试
- 要做 Agnes 单图或多图参考生成，并且当前流程明确允许 Agnes

建议：

- 通用 Agnes 文生图/参考图：`agnes-2.5`
- 只有兼容旧调用时再选 `agnes-2.1` / `agnes-2.0`

### H. 显式付费高质量 / 编辑

优先：`openai-image`（`gpt-image-2.5-sunburst`）

触发条件：

- 用户明确接受付费
- 用户明确指定 GPT 图像模型
- 免费链连续失败且用户允许切付费

---

## 四、视频路由规则

视频 API 保留 `agnes-video-v2.0` 作为兼容默认值。可显式选择 `agnes-video-2.5` 和 `agnes-video-2.5-flash`，调用前请核对渠道账号权限。

### 视频输入模式判定

| 模式 | 触发条件 | Agnes Video 2.5 字段 |
|---|---|---|
| `text` | 只有文字，没有图 | `mode=text` + `prompt` |
| `keyframe` | 有首帧和/或尾帧 | `first_frame` / `last_frame` + `mode=keyframe` |
| `reference` | 1～8 张普通参考图 | `images[]` + `mode=reference` |

2.5 的参考图必须是公开可访问的 `http(s)` URL。受保护 `/uploads/*` / `/generated/*` 资产可通过已配置的参考图中继供2.5使用；未配置中继时应说明条件，不因本地素材就自动切换到可用性待核实的旧型号。

### 降级逻辑

1. **first_last_frame** 无法直接支持时：
   - 先降级为 `first_frame`
   - 保留结束帧内容写进 prompt
2. **reference** 无法直接支持时：
   - 只保留最关键 1 张图作为 `image`
   - 其他参考内容改写进 prompt
3. **没有图**：
   - 自动走 `t2v`

---

## 五、快速路由决策表

| 用户需求 | 首选模型 | 备选 |
|---|---|---|
| 普通配图 / 不确定 | 默认链 | kolors |
| 中文海报 / 二次元 | qwen | kolors |
| 现实风格美女 / 写真 | z-turbo | flux |
| 商品图 / 风景 / 家居氛围 | flux | kolors |
| 创意脑洞 / 概念艺术 | z-image | qwen |
| 图生图 / 参考图 | kolors | qwen-edit / openai-image（按需求显式选） |
| Agnes 图片 | agnes-2.5 | agnes-2.1 / agnes-2.0 兼容 |
| 付费高质量图片/编辑 | openai-image | openai-flare（生成/编辑；实际权限独立验证） |
| 文生视频 | agnes-video-v2.0 | agnes-video-2.5（显式、需权限） |
| 参考图视频 | agnes-video-v2.0（本地受保护资产） | agnes-video-2.5 + reference（公网 URL、需权限） |
| 首尾帧视频 | agnes-video-v2.0 + keyframes | agnes-video-2.5 + keyframe（公网 URL、需权限） |

---

## 六、不要这样路由

- 不要把“现实风格美女”默认扔给 `qwen`
- 不要把“中文海报”默认扔给 `z-turbo`
- 不要把用户已经明确指定的付费模型改成免费模型
- 不要把视频任务塞进图片接口
- 不要看到参考图就机械全部传进去；先判断每张图是什么角色

## 渠道参数与任务处理

各渠道的参数限制、账户目录和任务处理见 [渠道输入和任务处理](MODEL_RESOLUTION_REFERENCE.md#渠道输入和任务处理)。
