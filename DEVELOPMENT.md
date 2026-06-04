# 开发说明

本文档给维护者和后续 Agent 使用，普通用户优先看 `README.md`。

## 设计原则

AngeMedia Gateway 的代码结构遵循一个原则：

> 对上游保持一个统一的 OpenAI-compatible 接口，对下游用 Provider Registry 接不同图片平台。

这样以后新增即梦、GPT 图片模型、自建 ComfyUI 或其他图片服务时，不需要改动上游调用方式。

## Provider Registry

每个图片渠道都应该独立封装成 provider adapter，并实现统一的 `generate()` 方法。

当前默认渠道：

- `SiliconFlowProvider`：硅基流动 Kolors；
- `ModelScopeProvider`：魔搭 Qwen / FLUX / Z-Image / Z-Turbo；
- `PollinationsProvider`：Pollinations 兜底；
- `OpenAICompatibleImageProvider`：兼容 OpenAI 图片接口的可选付费渠道。

新增渠道时建议补齐：

1. 渠道名称；
2. 环境变量；
3. 真实模型名；
4. 网关别名；
5. 请求参数映射；
6. 响应格式标准化；
7. 错误分类；
8. 是否需要本地缓存远程图片；
9. `/health` 状态；
10. README、README_CN、SKILL、.env.example 同步更新。

## 默认渠道和付费渠道

默认降级链只包含免费或轻量兜底渠道：

```text
kolors → qwen → flux → z-image → z-turbo → pollinations
```

`gpt-image-2` / `openai-image` 这类付费渠道必须显式调用，不应该进入默认链，避免普通请求误消耗付费额度。

## 魔搭异步任务头

魔搭图片任务提交和轮询阶段使用不同任务类型值：

```env
MODELSCOPE_SUBMIT_TASK_TYPE=text-to-image-generation
MODELSCOPE_POLL_TASK_TYPE=image_generation
```

这两个值不要因为名字不同就合并。提交阶段标识任务来源，轮询阶段查询统一的图片生成任务记录。

## 本地缓存策略

如果后端返回的是临时图片 URL，建议下载到本地 `OUTPUT_DIR`，再通过：

```text
/generated/文件名
```

对外提供。这样 NAS、Agent、New-API 访问会更稳定。

## 技能文档结构建议

从 v0.1.0 开始，技能文档建议拆层：

- `SKILL.md`：只放触发规则、主流程和索引；
- `docs/SKILL_IMAGE_GENERATION.md`：图片任务规则；
- `docs/SKILL_VIDEO_GENERATION.md`：视频任务规则；
- `docs/SKILL_MEDIA_ROUTING.md`：模型路由参考；
- `docs/SKILL_PROMPT_ENHANCEMENT.md`：提示词增强规范。

这样后面继续接入即梦、GPT 图片、其他视频模型时，主 Skill 不会越写越臃肿。

## 提交前检查

```bash
python3 -m py_compile scripts/proxy.py scripts/image-gateway/gateway.py scripts/angemedia_gateway/server.py scripts/angemedia_gateway/runtime.py scripts/angemedia_gateway/config_metadata.py scripts/angemedia_gateway/routes/*.py scripts/angemedia_gateway/adapters/agnes_video.py
git ls-files | grep -E 'blog-draft|cache|__pycache__|\.pyc'
```

第二条命令没有输出才算干净。


## 生成文件本地化

v0.1.0 增加了远端媒体自动下载逻辑：

- 图片成功后调用 `localize_image_result()`；
- 视频同步完成或异步轮询完成后调用 `localize_video_result()`；
- 本地文件统一放在 `OUTPUT_DIR`；
- 对外统一通过 `/generated/文件名` 访问。

新增 Provider 时，如果后端返回临时 URL，应优先复用这套本地化逻辑。

如果对象存储或 CDN 把真实图片/视频返回成 `application/octet-stream`，文件扩展名要优先使用原始 URL 后缀或调用方兜底后缀，避免把可预览媒体保存成 `.bin`。


## SQLite 本地记忆层

v0.1.0 增加本地 SQLite 数据库，用于保存：

- 配置项；
- 生成历史；
- 视频任务队列；
- 上传文件；
- Ange 小助手计划。

默认路径：

```text
~/.image-proxy/angemedia.db
```

开发时不要直接绕过数据库写文件状态，优先复用 `record_generation()`、`upsert_video_task()`、`/v1/uploads` 等接口。

## Ange 小助手

Ange 小助手是单轮媒体生成规划器，不是通用 Agent。

它应该只做：

- 路由；
- Prompt 增强；
- 图片/视频参数规划；
- 请求体生成。

不要让它修改配置、联网搜索或负责各平台媒体发送。


## 模块拆分

v0.1.0-refactor 后，旧入口仍保留：

```text
scripts/image-gateway/gateway.py
```

但真实实现已迁移到：

```text
scripts/angemedia_gateway/
```

主要模块：

```text
config.py           配置、路径、环境变量
config_metadata.py  管理后台中文配置元数据和保存前校验
runtime.py          共享运行时对象、鉴权依赖、上传写入限制
state.py            SQLite 配置、历史、上传、任务队列
schemas.py          Pydantic 请求模型
media.py            远端媒体本地化、OpenAI 图片响应格式
routing.py          模型路由、规则版 Prompt 增强
assistant.py        Ange 小助手 LLM 规划
providers/          图片 Provider
routes/             页面、管理、媒体、文件/历史路由
server.py           FastAPI app 装配，保持轻量
```

后续新增能力时，不要继续把逻辑塞回兼容入口。

## 管理配置中心

管理后台配置项由 `config_metadata.py` 统一描述，前端通过 `/v1/admin/config-metadata` 渲染中文标签、用途说明、字段类型和分组。新增配置项时需要同步：

1. `config.py` 的 `CONFIG_KEYS` / `SECRET_KEYS`；
2. `config_metadata.py` 的分组、字段说明和校验规则；
3. `.env.example`、`README_CN.md` 和必要的 Skill 文档；
4. 单元测试中的配置元数据覆盖。

不要在前端直接把环境变量名作为主标签展示。变量名只作为开发者排查标识保留。


## 独立 Skill 包

Agent 使用的 Skill 已从仓库根文档中拆出，放在：

```text
skill/
```

根目录的 `SKILL.md` 保持轻量，内容与 `skill/SKILL.md` 对齐，不包含 Web 管理后台和项目开发细节，避免干扰 Agent 生成图片/视频时的注意力。

发布时 GitHub Actions 会同时打包：

```text
angemedia-gateway-<version>.zip
angemedia-gateway-skill-<version>.zip
```

开发项目请用完整仓库；给 Agent 安装技能时，只用 `skill/` 包。


## VideoRequest 位置

统一视频请求模型 `VideoRequest` 位于 `scripts/angemedia_gateway/schemas.py`。视频 provider 只导入该模型，不再在 adapter 内定义请求 schema。
