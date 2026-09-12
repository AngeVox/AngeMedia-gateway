# Agnes 图片模型调用示例

> 本文档只描述 AngeMedia v0.2.12 已验证并实际发送的 Agnes 图片参数。上游出现新字段时，必须先同步 adapter、catalog 与测试，再更新本文档。

## 一、当前网关能力

| 模型别名 | 实际模型 | 当前定位 |
|---|---|---|
| `agnes-image` / `agnes-2.5` | `agnes-image-2.5-flash` | 当前推荐：文生图、1～4 张参考图、命名尺寸档位、宽高比 |
| `agnes-2.1` | `agnes-image-2.1-flash` | 兼容模型；请求合同与 2.5 当前接入保持一致 |
| `agnes-2.0` | `agnes-image-2.0-flash` | 兼容模型；自由尺寸、参考图、`seed` |

统一入口：`POST /v1/images/generations`。

## 二、Agnes Image 2.5 文生图

```json
{
  "model": "agnes-2.5",
  "prompt": "高级产品摄影，一台极简白色无线音箱放在石材桌面上，柔和自然光，浅景深，干净背景。不要文字和水印。",
  "size": "2K",
  "aspect_ratio": "1:1",
  "response_format": "url"
}
```

当前允许的命名尺寸档位：`1K`、`2K`、`3K`、`4K`。

当前宽高比：`1:1`、`3:4`、`4:3`、`16:9`、`9:16`、`2:3`、`3:2`、`21:9`。

为兼容旧调用，catalog 还保留 `1024x768`、`1024x1024`、`768x1024`、`1280x720`、`720x1280`、`1536x1024`、`1024x1536`、`4096x4096` 等已验证值。自由尺寸安全边界由 catalog 统一校验：边长 `512–4096`，像素总量 `262144–16777216`。

## 三、单图 / 多图参考

统一请求可以使用旧 `image` 字段，也可以使用新的有序 `reference_images[]`；Agnes adapter 会安全物化网关自有资产或 data URL，并按 Agnes 当前数组格式发送。总数最多 4 张。

```json
{
  "model": "agnes-2.5",
  "prompt": "保留人物身份和构图，把画面改成冷色电影海报风格。",
  "image": "/uploads/reference.png",
  "size": "2K",
  "aspect_ratio": "16:9",
  "response_format": "url"
}
```

```json
{
  "model": "agnes-2.5",
  "prompt": "结合人物造型和场景氛围，输出统一风格的商业海报。",
  "reference_images": [
    "/uploads/look.png",
    "/uploads/scene.png"
  ],
  "size": "2K",
  "aspect_ratio": "4:3",
  "response_format": "url"
}
```

`/uploads/*`、`/generated/*`、安全 data URL 和允许的公开 URL 会继续经过现有参考图安全边界；不要绕过 materialization / SSRF / 大小限制。

## 四、Agnes Image 2.0 兼容调用

2.0 仍保留 `seed`：

```json
{
  "model": "agnes-2.0",
  "prompt": "梦幻插画风格，一座漂浮在云海中的图书馆，金色晨光。",
  "size": "1024x1024",
  "seed": 42,
  "response_format": "url"
}
```

2.0 当前边界：边长 `512–2048`，最大像素数 `3,145,728`。

## 五、返回 Base64

需要直接拿图片内容时可请求：

```json
{
  "model": "agnes-2.5",
  "prompt": "深蓝色科技品牌主视觉，中心是发光的数据枢纽。",
  "size": "2K",
  "aspect_ratio": "1:1",
  "response_format": "b64_json"
}
```

## 六、严格参数边界

| 模型 | 当前文生图参数 | 当前参考图参数 |
|---|---|---|
| Agnes Image 2.5 | `prompt`、`size`、`aspect_ratio`、`response_format` | 前述参数 + `image` / `reference_images` |
| Agnes Image 2.1 | 与当前 2.5 接入合同一致 | 与当前 2.5 接入合同一致 |
| Agnes Image 2.0 | `prompt`、`size`、`seed`、`response_format` | 前述参数 + `image` / `reference_images` |

`mask`、`strength`、`negative_prompt`、`guidance_scale`、`num_inference_steps` 等没有进入当前 Agnes 发布合同。需要接入时必须先取得真实上游证据。

## 七、模型选择

- 新 Agnes 图片请求：优先 `agnes-image` / `agnes-2.5`。
- 旧 2.1 / 2.0 调用仍可继续使用。
- 需要固定随机种子：当前显式选择 `agnes-2.0`。
- Agnes 图片不进入默认免费降级链，必须显式选择。
