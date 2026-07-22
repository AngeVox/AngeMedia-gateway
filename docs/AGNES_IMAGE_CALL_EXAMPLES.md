# Agnes 图片模型调用示例

> 本文档只描述 AngeMedia v0.2.11 已验证并实际发送的 Agnes 图片参数，不把上游可能存在但尚未接入的字段写成可用能力。
> Agnes 文档入口：`https://agnes-ai.com/zh-Hans/docs/overview`。上游接口继续变化时，应同时更新 adapter、catalog、测试和本文档。

## 一、当前网关能力

| 模型别名 | 实际模型 | 已接入能力 |
|---|---|---|
| `agnes-image` / `agnes-2.1` | `agnes-image-2.1-flash` | 文生图、1～4 张参考图、`1K`～`4K` 尺寸档位、宽高比 |
| `agnes-2.0` | `agnes-image-2.0-flash` | 文生图、1～4 张参考图、自由尺寸、`seed` |

统一入口：

```text
POST /v1/images/generations
```

配置了 Gateway API Key 时，请带：

```http
Authorization: Bearer <GATEWAY_API_KEY>
```

## 二、Agnes Image 2.1 文生图

Agnes Image 2.1 当前推荐使用命名尺寸档位，并通过 `aspect_ratio` 指定比例。网关会把它转换为 Agnes 的 `ratio` 字段。

```bash
curl -X POST http://localhost:9890/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agnes-2.1",
    "prompt": "高级产品摄影，一台极简白色无线音箱放在石材桌面上，柔和自然光，浅景深，干净背景，商业广告质感。不要文字和水印。",
    "size": "2K",
    "aspect_ratio": "1:1",
    "response_format": "url"
  }'
```

支持的尺寸档位：

```text
1K, 2K, 3K, 4K
```

支持的宽高比：

```text
1:1, 3:4, 4:3, 16:9, 9:16, 2:3, 3:2, 21:9
```

为兼容旧客户端，网关仍接受 catalog 中列出的部分 `WIDTHxHEIGHT` 值；新调用优先使用命名尺寸档位。

## 三、Agnes Image 2.0 文生图

Agnes Image 2.0 使用 `WIDTHxHEIGHT`，并支持 `seed`：

```bash
curl -X POST http://localhost:9890/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agnes-2.0",
    "prompt": "梦幻插画风格，一座漂浮在云海中的图书馆，金色晨光，柔和色彩，细节丰富，适合文章封面。不要文字和水印。",
    "size": "1024x1024",
    "seed": 42,
    "response_format": "url"
  }'
```

常用预设：

```text
1024x768, 1024x1024, 768x1024, 1280x720, 2048x1536
```

自由尺寸必须落在 catalog 声明的边界内；Agnes Image 2.0 最大边长为 2048，最大像素数为 3,145,728。

## 四、单图参考

`image` 可以是受控的 `/generated/*`、`/uploads/*` 路径，或安全的图片 URL / data URL。网关会先验证并物化参考图，再按 Agnes 当前格式发送。

```json
{
  "model": "agnes-2.1",
  "prompt": "保留主体轮廓和构图，把画面改成冷色电影海报风格，体积光明显，细节自然。",
  "image": "/uploads/reference.png",
  "size": "2K",
  "aspect_ratio": "16:9",
  "response_format": "url"
}
```

## 五、多图参考

当前两个 Agnes 图片模型最多接受 4 张参考图。可以使用 `images`：

```json
{
  "model": "agnes-2.0",
  "prompt": "结合第一张图的人物造型和第二张图的场景氛围，输出统一风格的商业海报。",
  "images": [
    "/uploads/look.png",
    "/uploads/scene.png"
  ],
  "size": "1024x1024",
  "seed": 42,
  "response_format": "url"
}
```

`image` 与 `images` 可以由网关统一收集，但总数不得超过 4。

## 六、返回 base64

不方便读取远端 URL 时，可以请求 `b64_json`：

```json
{
  "model": "agnes-2.1",
  "prompt": "深蓝色科技品牌主视觉，中心是发光的数据枢纽，极简构图。",
  "size": "2K",
  "aspect_ratio": "1:1",
  "response_format": "b64_json"
}
```

## 七、严格参数边界

AngeMedia v0.2.11 不会任意透传 Agnes 图片字段。当前允许范围是：

| 模型 | 文生图参数 | 图生图参数 |
|---|---|---|
| Agnes Image 2.1 | `prompt`、`size`、`aspect_ratio`、`response_format` | 前述参数 + `image` / `images` |
| Agnes Image 2.0 | `prompt`、`size`、`seed`、`response_format` | 前述参数 + `image` / `images` |

`strength`、`mask`、`negative_prompt`、`guidance_scale`、`num_inference_steps` 等未验证字段不会作为当前 Agnes 发布合同。需要接入新字段时，应先取得真实 API 证据，再同时扩展 schema、catalog、adapter、Studio 控件和测试。

## 八、模型选择

- 普通高质量文生图：优先 `agnes-2.1`。
- 需要宽高比档位：使用 `agnes-2.1` 的 `size` + `aspect_ratio`。
- 需要固定随机种子：使用 `agnes-2.0`。
- 单图或多图参考：两个模型均可测试，最多 4 张。
- Agnes 图片不进入默认图片降级链，必须显式选择。
