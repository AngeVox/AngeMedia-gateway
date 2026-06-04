# Agnes 图片模型调用示例

> 本文档专门说明 Agnes 图片模型的调用方式。目标不是照搬官方文档，而是给出够用、精简、可直接照抄的示例。
> 官方文档入口：`https://agnes-ai.com/doc`。如果 Agnes 后续调整字段名或能力边界，请以官方文档为准。

## 一、这份文档覆盖什么

当前整理的 Agnes 图片能力主要包括：

1. 文生图（Text-to-Image）
2. 图生图（Image-to-Image）
3. 多图参考 / 多图编辑
4. 局部重绘 / 局部编辑（带 `mask`）
5. 轻度改图 / 风格改写
6. 返回 `url` 或 `b64_json`
7. 常用高级参数的传法

## 二、当前网关中的 Agnes 图片别名

| 别名 | 实际模型 | 说明 |
|---|---|---|
| `agnes-image` | `AGNES_IMAGE_MODEL` 环境变量指定，默认 `agnes-image-2.1-flash` | Agnes 图片默认别名 |
| `agnes-2.1` | `agnes-image-2.1-flash` | 推荐优先使用，当前主推 |
| `agnes-2.0` | `agnes-image-2.0-flash` | 兼容模型，适合需要多图/编辑场景时测试 |

## 三、统一调用入口

网关仍然统一使用：

```text
POST /v1/images/generations
```

Agnes 图片相关的高级字段，会由网关透传给 Agnes 后端。

如果配置了网关密钥，请带：

```http
Authorization: Bearer <GATEWAY_API_KEY>
```

或：

```http
X-API-Key: <GATEWAY_API_KEY>
```

## 四、文生图示例

### 1）Agnes Image 2.1：通用高质量文生图

```bash
curl -X POST http://localhost:9890/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agnes-2.1",
    "prompt": "高级产品摄影，一台极简白色无线音箱放在石材桌面上，柔和自然光，浅景深，干净背景，商业广告质感。不要：文字、水印、杂乱背景。",
    "size": "1024x1024",
    "response_format": "url"
  }'
```

### 2）Agnes Image 2.0：插画 / 概念图

```bash
curl -X POST http://localhost:9890/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agnes-2.0",
    "prompt": "梦幻插画风格，一座漂浮在云海中的图书馆，金色晨光，柔和色彩，细节丰富，适合文章封面。不要：水印、文字。",
    "size": "1024x1024",
    "response_format": "url"
  }'
```

## 五、图生图示例

### 1）单图参考改写

适合：保留主体和大致构图，同时改风格、改光影、改氛围。

```bash
curl -X POST http://localhost:9890/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agnes-2.1",
    "prompt": "保留主体轮廓和构图，把这张图改成高级电影感海报风格，冷色调，体积光明显，细节更丰富。",
    "image": "https://example.com/input.jpg",
    "strength": 0.55,
    "size": "1024x1024",
    "response_format": "url"
  }'
```

### 2）轻度修饰 / 修图

适合：不大幅改变主体，只做提质、清晰化、风格轻调。

```json
{
  "model": "agnes-2.1",
  "prompt": "提升清晰度和质感，保留原始人物外观，只优化光线、肤色和背景层次，不要新增元素。",
  "image": "https://example.com/portrait.jpg",
  "strength": 0.25,
  "size": "1024x1024",
  "response_format": "url"
}
```

## 六、多图参考 / 多图编辑示例

Agnes 2.0 的公开说明里提到支持多图相关能力，因此网关也允许直接透传 `images` 一类字段。

### 1）多图融合

```json
{
  "model": "agnes-2.0",
  "prompt": "把第一张图的人物服装风格和第二张图的场景氛围融合，输出一张统一风格的商业海报图，构图完整，自然真实。",
  "images": [
    "https://example.com/look.jpg",
    "https://example.com/scene.jpg"
  ],
  "size": "1024x1024",
  "response_format": "url"
}
```

### 2）多图参考后统一风格

```json
{
  "model": "agnes-2.0",
  "prompt": "参考多张输入图片，统一成极简高级家居品牌视觉，米白色调，柔和自然光，商业摄影风格。",
  "images": [
    "https://example.com/ref1.jpg",
    "https://example.com/ref2.jpg",
    "https://example.com/ref3.jpg"
  ],
  "size": "1024x1024"
}
```

## 七、局部重绘 / 局部编辑示例

如果 Agnes 后端支持 `mask` 局部编辑，可以直接透传。常见用法是：

- `image`：原图
- `mask`：需要修改的区域蒙版
- `prompt`：说明要改什么

```json
{
  "model": "agnes-2.1",
  "prompt": "把选中区域替换成一束白色郁金香，光线自然，和原图整体风格一致。",
  "image": "https://example.com/original.jpg",
  "mask": "https://example.com/mask.png",
  "size": "1024x1024",
  "response_format": "url"
}
```

如果官方后续要求字段名是 `input_image`、`input_images` 或其他命名，也可以直接透传。

## 八、返回 base64 示例

如果客户端不方便取远端图片 URL，可以让网关直接转成 `b64_json`：

```json
{
  "model": "agnes-2.1",
  "prompt": "深蓝色科技风品牌主视觉图，中心是发光的数据枢纽，极简构图。",
  "size": "1024x1024",
  "response_format": "b64_json"
}
```

## 九、常用高级参数

下面这些字段，当前网关会一并透传给 Agnes 后端：

| 字段 | 作用 | 常见场景 |
|---|---|---|
| `negative_prompt` | 负面提示词 | 去掉水印、畸形、低质感 |
| `seed` | 固定随机种子 | 需要复现结果时 |
| `image` | 单图输入 | 图生图、单图编辑 |
| `images` | 多图输入 | 多图参考、风格融合 |
| `mask` | 局部编辑蒙版 | 局部重绘 |
| `strength` | 输入图影响强度 | 改图幅度控制 |
| `guidance_scale` | 提示词引导强度 | 控制对 prompt 的遵循度 |
| `num_inference_steps` | 推理步数 | 平衡速度与质量 |

说明：是否全部生效，由 Agnes 后端实际支持情况决定；网关负责透传，不负责强校验。

## 十、建议怎么选模型

- **普通高质量文生图**：优先 `agnes-2.1`
- **图生图 / 编辑 / 多图实验**：优先先试 `agnes-2.0`，再试 `agnes-2.1`
- **需要更稳定的通用主力链**：不用 Agnes，走默认免费链 `kolors → qwen → flux → z-image → z-turbo → pollinations`
- **只是想要高质量商业图且可接受付费**：也可以考虑 `gpt-image-2`

## 十一、注意事项

1. Agnes 图片能力不进入默认免费降级链，必须显式指定 `agnes-2.1`、`agnes-2.0` 或 `agnes-image`。
2. 如果用户只说“画一张图”，不要自动切 Agnes；除非用户明确要求 Agnes，或当前宿主希望优先测试 Agnes。
3. 如果局部编辑、图生图请求失败，先检查：
   - `AGNES_API_KEY` 是否配置；
   - 传入的 `image` / `mask` 是否是 Agnes 可访问的 URL 或官方支持的数据格式；
   - 字段名是否和 Agnes 当期文档一致。
