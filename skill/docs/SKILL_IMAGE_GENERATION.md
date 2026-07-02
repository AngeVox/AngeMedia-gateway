# 图片生成子技能

> 本文档处理“Agent 如何更聪明地生图”。重点不是图片 API 本身，而是：**怎么理解用户意图、怎么选模型、怎么补提示词、怎么提交给网关。**

## 一、图片任务工作流

1. 判断任务是不是图片任务。
2. 识别需求类型：海报、封面、头像、商品图、风景、人物写真、二次元插画、概念艺术、图生图。
3. 提取硬约束：
   - 主体
   - 风格
   - 场景
   - 比例 / 尺寸
   - 画面文字
   - 不能出现的内容
4. 根据 `docs/SKILL_MEDIA_ROUTING.md` 选模型。
5. 根据 `docs/SKILL_PROMPT_ENHANCEMENT.md` 增强提示词。
6. 生成最终请求体。
7. 提交到 `/v1/images/generations`。
8. 如果失败，优先换模型或微调提示词，不要让用户从头再说。

---

## 二、模型选择

图片模型路由不要在本文件重复维护。执行图片任务时，按 `docs/SKILL_MEDIA_ROUTING.md` 的能力矩阵、别名表和 Cost-first 规则选择模型。

---


---

## 三、尺寸选择速查

详细尺寸上限和来源见 `docs/MODEL_RESOLUTION_REFERENCE.md`。图片任务里 Agent 先按下面的安全默认值走：

| 场景 | 推荐模型 | 推荐尺寸 | 备注 |
|---|---|---|---|
| 通用图 | 默认链 / `kolors` | `1024x1024` | 最稳 |
| 竖屏海报 | `kolors` | `960x1280` 或 `768x1024` | Kolors 官方固定尺寸 |
| 手机竖屏 | `kolors` | `720x1280` | 9:16 |
| 中文海报 / 带字图 | `qwen` | 由 ModelScope 决定 | ModelScope Provider 当前不强传尺寸 |
| 写实人像 | `z-turbo` | 由 ModelScope 决定 | ModelScope Provider 当前不强传尺寸 |
| Agnes 普通图 | `agnes-2.1` | `1024x1024` 或 `1024x768` | 效果和速度更稳 |
| Agnes 高清图 | `agnes-2.1` | `2048x1536` | 项目实测可用，生成更慢 |
| Agnes 方形超大图 | `agnes-2.1` | 不建议 `2048x2048` | 实测容易超时 |
| OpenAI 付费图 | `gpt-image-2` | `1024x1024` / `1536x1024` / `1024x1536` | 更大尺寸按官方/服务商约束 |

不要把用户随口说的“高清”自动理解成必须最大尺寸。只有用户明确说“4K、打印、大幅海报、高清大图”时，再考虑更大尺寸。

## 四、图生图与编辑任务

如果用户上传了图片，不要直接当纯文生图处理。

### 图生图

适用：

- “参考这张图重画”
- “把这张图改成插画风”
- “保留构图，改成电影感”

优先思路：

- 如果当前要走 Agnes，参考 `docs/AGNES_IMAGE_CALL_EXAMPLES.md`
- 否则当前默认链更偏文生图；需要重编辑能力时可建议切 Agnes

### 局部编辑 / 重绘

适用：

- “把图里的花换成白色郁金香”
- “只改背景，不改人物”

需要：

- 主图角色：`subject`
- 蒙版角色：`mask`

---

## 五、推荐请求结构

### A. 通用图片

```json
{
  "model": "qwen",
  "prompt": "现代科技风中文公众号封面，深蓝色未来感背景，中心是发光的 AI AngeMedia 网关枢纽图标，周围有模型路由线条和图片卡片。标题文字：『给 AI Agent 接上文生图』，标题位于上方居中，粗体中文字体，高对比度，留白充足，4:3 构图，不要人物，不要水印。",
  "size": "1024x1024",
  "response_format": "url"
}
```

### B. 写实摄影

```json
{
  "model": "z-turbo",
  "prompt": "写实电影感人像摄影，一位成年女性模特站在雨夜城市街头，霓虹灯和湿润路面反射在背景中，柔和轮廓光，自然皮肤质感，85mm 镜头感，浅景深，半身构图，高级商业摄影风格。不要：文字、水印、夸张塑料感。",
  "size": "1024x1024",
  "response_format": "url"
}
```

### C. 显式 Agnes 图生图

```json
{
  "model": "agnes-2.1",
  "prompt": "保留主体轮廓和整体构图，把这张图改成高级电影感海报风格，冷色调，体积光明显，细节更丰富。",
  "image": "https://example.com/input.jpg",
  "strength": 0.55,
  "size": "1024x1024",
  "response_format": "url"
}
```

---

## 六、失败重试规则

1. 默认链失败：允许自动切换到链上的下一个模型。
2. `qwen` 不理想：可以改 `flux` 或 `z-image`。
3. 写实图不理想：优先换 `z-turbo`。
4. 需要编辑能力但失败：建议切到 Agnes。
5. 始终不要要求用户完整重写需求，Agent 应复用已增强的 prompt 再试。


---

## 六、本地化返回

图片生成成功后，网关默认会尝试把远端 URL 下载到 `OUTPUT_DIR`，并把 `data[0].url` 改成本地稳定地址：

```text
/generated/xxx.png
```

响应里可能同时包含：

```json
{
  "url": "http://localhost:9890/generated/xxx.png",
  "remote_url": "[server-internal temporary URL redacted]",
  "localized": true
}
```

Agent 应优先使用本地化后的 `url` 给用户发送文件，不要展示远端临时 URL 或本地 filesystem path。
