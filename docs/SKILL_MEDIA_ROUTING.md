# 媒体模型路由参考（图片 + 视频）

> 这份文档给 Agent 做“先判断、再路由”使用。目标不是死记模型，而是让 Agent 能根据任务类型，按**能力矩阵 + 成本优先**做决策。

## 一、路由总原则

### 1. Cost-first

默认优先使用免费或低成本链路，不要一上来就走付费模型。

图片默认链：

```text
kolors → qwen → flux → z-image → z-turbo → pollinations
```

视频当前主力：

```text
agnes-video-v2.0
```

### 2. 适配度优先于机械默认

如果任务特征非常明确，应直接选更适合的模型，而不是一律走默认链。

### 3. 用户显式指定优先

如果用户明确说：

- “用 Agnes 生图”
- “用 gpt-image-2”
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

### 图片模型别名表

| 别名 | 渠道 | 实际模型 | 推荐场景 |
|---|---|---|---|
| `kolors` | SiliconFlow | `Kwai-Kolors/Kolors` | 默认主力通用图 |
| `siliconflow` | SiliconFlow | 同 `kolors` | 兼容别名，新请求优先用 `kolors` |
| `qwen` | ModelScope | `Qwen/Qwen-Image-2512` | 中文海报、二次元、带字图片 |
| `flux` | ModelScope | `black-forest-labs/FLUX.1-Krea-dev` | 产品图、风景、自然光、摄影感场景 |
| `z-image` | ModelScope | `Tongyi-MAI/Z-Image` | 创意概念、超现实、艺术实验 |
| `z-turbo` | ModelScope | `Tongyi-MAI/Z-Image-Turbo` | 写实人像、商业摄影、快速出图 |
| `pollinations` | Pollinations | 默认 `zimage` | 最后兜底和轻量测试 |
| `agnes-image` / `agnes-2.1` | Agnes AI | `agnes-image-2.1-flash` | 显式调用，Agnes 高质量文生图 |
| `agnes-2.0` | Agnes AI | `agnes-image-2.0-flash` | 显式调用，兼容多图/编辑实验 |
| `gpt-image-2` / `openai-image` | OpenAI-compatible | 由 `OPENAI_IMAGE_MODEL` 配置 | 显式付费高质量，不进默认链 |

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

### F. 显式 Agnes 图片能力

优先：`agnes-2.1` 或 `agnes-2.0`

适用特征：

- 用户明确要 Agnes
- 要做 Agnes 风格对比测试
- 要图生图、多图参考、局部重绘，并且当前流程要走 Agnes

建议：

- 通用高质量文生图：`agnes-2.1`
- 多图参考 / 图像编辑实验：优先试 `agnes-2.0`

### G. 显式付费高质量

优先：`gpt-image-2` / `openai-image`

触发条件：

- 用户明确接受付费
- 用户明确指定 GPT 图像模型
- 免费链连续失败且用户允许切付费

---

## 四、视频路由规则

当前主力视频模型：`agnes-video-v2.0`

### 视频输入模式判定

| 模式 | 触发条件 | 建议字段 |
|---|---|---|
| `t2v` | 只有文字，没有图 | `prompt` |
| `first_frame` | 只有 1 张起始图 | `image` |
| `first_last_frame` | 有开始图和结束图，要平滑过渡 | `images` + `mode=keyframes` |
| `reference` | 有参考图，但不是严格首尾帧 | 优先归并到 `images` / `extra_body`，如果目标模型不支持就降级 |

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
| 普通配图 / 不确定 | 默认链 | pollinations |
| 中文海报 / 二次元 | qwen | kolors |
| 现实风格美女 / 写真 | z-turbo | flux |
| 商品图 / 风景 / 家居氛围 | flux | kolors |
| 创意脑洞 / 概念艺术 | z-image | qwen |
| Agnes 图片测试 | agnes-2.1 / agnes-2.0 | 无 |
| 付费高质量图片 | gpt-image-2 | 无 |
| 文生视频 | agnes-video-v2.0 | 无 |
| 图生视频 | agnes-video-v2.0 | 无 |
| 首尾帧视频 | agnes-video-v2.0 + keyframes | 无 |

---

## 六、不要这样路由

- 不要把“现实风格美女”默认扔给 `qwen`
- 不要把“中文海报”默认扔给 `z-turbo`
- 不要把用户已经明确指定的付费模型改成免费模型
- 不要把视频任务塞进图片接口
- 不要看到参考图就机械全部传进去；先判断每张图是什么角色
