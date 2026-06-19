# AngeMedia Gateway：给 Agent 接上图片与视频生成能力

[English](README.md) | [简体中文](README_CN.md)

> 当前版本：v0.2.0。面向 AI Agent、NAS、New-API 和自托管工作流的本地图片/视频生成网关。默认降级链以硅基流动、魔搭为主；OpenAI-compatible、Agnes 图片、Agnes 视频等适配能力保留；Pollinations 为实验性且缺省关闭。对外提供兼容 OpenAI Images 的统一接口。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-ready-009688.svg)](https://fastapi.tiangolo.com/)

---
## 先看结论

这个项目解决的是一个很实际的问题：让 Agent 只调用一个接口，就能使用多个图片生成渠道。

```text
AI Agent / New-API / OpenAI SDK
        ↓
POST http://你的服务器:9890/v1/images/generations
        ↓
AngeMedia Gateway
        ↓
硅基流动 Kolors → 魔搭 Qwen / FLUX / Z-Image
        ↓
返回图片 URL 或 b64_json
```

默认降级链：

```text
kolors → qwen → flux → z-image → z-turbo
```

付费渠道，比如 `gpt-image-2` / `openai-image`，已经预留，但**不会进入默认降级链**，避免误消耗付费额度。

Pollinations 为实验性渠道，缺省关闭，不在默认降级链中。如需启用，在 `.env` 中设置 `BUILTIN_PROVIDER_POLLINATIONS_ENABLED=true`。

---

## 新用户最先要做什么

默认三个图片渠道已经写进代码里了，不需要改代码，只需要把密钥填进 `.env`。

### 推荐配置顺序

| 顺序 | 渠道 | 是否建议配置 | 作用 |
|---|---|---|---|
| 1 | 硅基流动 SiliconFlow | 强烈建议 | 启用 `kolors` 主力通道 |
| 2 | 魔搭 ModelScope | 建议 | 启用 `qwen`、`flux`、`z-image`、`z-turbo` |
| 3 | Pollinations | 实验性，缺省关闭 | 不在默认降级链中，需手动启用 |

最少配置方式：**只填 `SILICONFLOW_API_KEY` 或 `MODELSCOPE_API_KEY` 其中一个就能启动测试**。如果两个都填，体验更稳。

---

## 三个默认渠道怎么注册和拿 Key

### 1. 硅基流动 SiliconFlow

- 注册入口：`https://cloud.siliconflow.cn`
- 官方快速开始文档：`https://docs.siliconflow.cn/en/userguide/quickstart`
- 需要填写的变量：`SILICONFLOW_API_KEY`
- 启用的模型别名：`kolors` / `siliconflow`

获取步骤：

1. 打开 `https://cloud.siliconflow.cn` 注册并登录。
2. 进入控制台的 **API Keys** 页面。
3. 点击创建密钥。
4. 复制生成的密钥，填入 `.env`：

```env
SILICONFLOW_API_KEY=你的硅基流动密钥
```

说明：官方文档里写的是进入 API Keys 页面后创建 API Key；具体额度、价格、免费规则会变，以控制台和价格页为准。

### 2. 魔搭 ModelScope

- 注册入口：`https://modelscope.cn`
- API-Inference 介绍：`https://modelscope.cn/docs/model-service/API-Inference/intro`
- 使用限制说明：`https://modelscope.cn/docs/model-service/API-Inference/limits`
- 需要填写的变量：`MODELSCOPE_API_KEY`
- 启用的模型别名：`qwen`、`flux`、`z-image`、`z-turbo`

获取步骤：

1. 打开 `https://modelscope.cn` 注册并登录。
2. 进入个人中心，找到 **访问令牌 / Access Token**。
3. 创建一个新令牌并复制。
4. 填入 `.env`：

```env
MODELSCOPE_API_KEY=你的魔搭访问令牌
```

说明：魔搭官方 API-Inference 是面向注册用户的免费推理服务。我们实测图片生成通道按所有图片模型共享约 50 张/天做本地保护，所以默认：

```env
MODELSCOPE_DAILY_LIMIT=50
```

真实额度仍以魔搭平台返回为准。

### 3. Pollinations（实验性，缺省关闭）

- 官网：`https://pollinations.ai`
- 密钥入口：`https://enter.pollinations.ai`
- API 文档：`https://github.com/pollinations/pollinations/blob/main/APIDOCS.md`
- 需要填写的变量：`POLLINATIONS_API_KEY`
- 启用的模型别名：`pollinations`
- 缺省状态：**关闭**，需手动设置 `BUILTIN_PROVIDER_POLLINATIONS_ENABLED=true` 启用

用法：

- Pollinations 不在默认降级链中，需要手动启用后才能使用。
- 启用后，不填 `POLLINATIONS_API_KEY`：网关会尝试旧公共图片接口。
- 填写 `POLLINATIONS_API_KEY`：优先走新版 OpenAI-compatible 图片接口。

```env
BUILTIN_PROVIDER_POLLINATIONS_ENABLED=true
POLLINATIONS_API_KEY=你的 Pollinations 密钥
```

---

## 快速开始

```bash
git clone https://github.com/ang77712829/angemedia-gateway.git
cd angemedia-gateway

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export ADMIN_USERNAME=admin
export ADMIN_DEFAULT_PASSWORD='换成一个足够长的随机密码'
python -m uvicorn scripts.angemedia_gateway.server:app --host 127.0.0.1 --port 9890
```

健康检查：

```bash
curl http://localhost:9890/health
```

生成图片：

```bash
curl -X POST http://localhost:9890/v1/images/generations   -H "Content-Type: application/json"   -d '{"prompt":"一只戴墨镜的猫，赛博朋克风格"}'
```

指定模型：

```bash
curl -X POST http://localhost:9890/v1/images/generations   -H "Content-Type: application/json"   -d '{"model":"qwen","prompt":"夕阳下的山脉","size":"1024x1024"}'
```

如果配置了 `GATEWAY_API_KEY`：

```bash
curl -X POST http://localhost:9890/v1/images/generations   -H "Authorization: Bearer 你的网关访问密钥"   -H "Content-Type: application/json"   -d '{"model":"z-turbo","prompt":"电影光感人像写真"}'
```

---

## 完整 `.env` 示例

```env
# 硅基流动：启用 kolors 主力通道
SILICONFLOW_API_KEY=你的硅基流动密钥

# 魔搭：启用 qwen / flux / z-image / z-turbo
MODELSCOPE_API_KEY=你的魔搭访问令牌

# Pollinations：实验性，缺省关闭，需手动启用
# BUILTIN_PROVIDER_POLLINATIONS_ENABLED=true
POLLINATIONS_API_KEY=

# 可选付费图片渠道：显式指定 gpt-image-2 / openai-image 时才会调用
OPENAI_IMAGE_API_KEY=
OPENAI_IMAGE_BASE_URL=https://api.openai.com/v1
OPENAI_IMAGE_MODEL=gpt-image-2

# 网关访问密钥，局域网多人或公网部署时建议填写
GATEWAY_API_KEY=换成一个足够长的随机字符串

PROXY_HOST=0.0.0.0
PROXY_PORT=9890
PUBLIC_BASE_URL=http://你的服务器IP:9890
IMAGE_PROXY_STATE_DIR=/data
MODELSCOPE_DAILY_LIMIT=50
POLLINATIONS_MODEL=zimage
HTTP_TIMEOUT=60
MAX_POLL_TIME=120
POLL_INTERVAL=3
```

---

## 后台运行

### Docker Compose

生产镜像内监听 `8000`，仓库根目录的 `docker-compose.yml` 默认映射为 `9892:8000`。启动前必须显式设置后台初始密码和 Gateway API Key，不要把示例占位值用于生产。

```bash
export ADMIN_USERNAME=admin
export ADMIN_DEFAULT_PASSWORD='换成一个足够长的随机密码'
export GATEWAY_API_KEY='换成一个足够长的随机网关密钥'
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f
```

访问地址：

```text
http://localhost:9892/
```

运行态数据通过命名卷持久化到容器内 `/app/state`、`/app/generated`、`/app/uploads`。镜像不会内置任何 provider key；只在需要时通过环境变量显式启用并填写对应 provider。

### systemd

创建服务文件：

```bash
sudo tee /etc/systemd/system/angemedia-gateway.service > /dev/null <<'EOF'
[Unit]
Description=AngeMedia Gateway
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/angemedia-gateway
EnvironmentFile=/opt/angemedia-gateway/.env
ExecStart=/opt/angemedia-gateway/.venv/bin/python /opt/angemedia-gateway/scripts/proxy.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

启用服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now angemedia-gateway
sudo systemctl status angemedia-gateway
```

---

## 接入 Agent / New-API

### 通用 Agent

让 Agent 调用：

```text
POST http://你的服务器IP:9890/v1/images/generations
```

请求体示例：

```json
{
  "model": "qwen",
  "prompt": "增强后的最终提示词",
  "size": "1024x1024",
  "response_format": "url"
}
```

建议把 `SKILL.md` 放进 Agent 的技能目录，让 Agent 学会：

1. 先理解用户意图；
2. 自动选择模型别名；
3. 扩写或润色提示词；
4. 再调用网关。

### OpenClaw / Hermes

不同 Agent 的技能目录不一样，不建议文档写死某个路径。通用做法是：

1. 把 `SKILL.md` 放到该 Agent 的技能目录；
2. 在 Agent 的工具配置里写入网关地址；
3. 如果配置了 `GATEWAY_API_KEY`，把它作为工具密钥保存；
4. 让 Agent 调用 `/v1/images/generations`。

### New-API

新增一个 OpenAI-compatible 渠道：

```text
Base URL: http://你的服务器IP:9890
API Key: 如果配置了 GATEWAY_API_KEY，就填这个值
模型：kolors, qwen, flux, z-image, z-turbo, gpt-image-2
```

---

## 模型别名

| 别名 | 渠道 | 实际模型 | 推荐场景 |
|---|---|---|---|
| `kolors` | 硅基流动 | `Kwai-Kolors/Kolors` | 通用文生图、中英文提示词、默认主力通道 |
| `siliconflow` | 硅基流动 | 同 `kolors` | 兼容别名，建议新请求用 `kolors` |
| `qwen` | 魔搭 | `Qwen/Qwen-Image-2512` | 中文海报、带字图片、复杂指令、二次元/插画 |
| `flux` | 魔搭 | `black-forest-labs/FLUX.1-Krea-dev` | 摄影感、自然光、产品氛围图、风景 |
| `z-image` | 魔搭 | `Tongyi-MAI/Z-Image` | 创意艺术、超现实概念、多样构图 |
| `z-turbo` | 魔搭 | `Tongyi-MAI/Z-Image-Turbo` | 写实人像、商业摄影、快速出图 |
| `pollinations` | Pollinations | 默认 `zimage` | 实验性，缺省关闭，不在默认降级链中 |
| `gpt-image-2` | 兼容 OpenAI 图片接口 | 通过 `OPENAI_IMAGE_MODEL` 配置 | 显式付费路由，不在默认链中 |

---

## API

### 生成图片

```http
POST /v1/images/generations
```

请求体：

```json
{
  "model": "qwen",
  "prompt": "赛博朋克城市夜景，电影光感",
  "size": "1024x1024",
  "response_format": "url"
}
```

`response_format` 支持：

- `url`
- `b64_json`

当前每次请求返回一张图片。

### 健康检查

```http
GET /health
```

返回最小健康状态，例如 `{"status":"ok"}`，不返回密钥、账号、渠道明细或本地配额细节。

### 模型列表

```http
GET /v1/models
```

返回 OpenAI 风格的模型列表。

---

## 常见问题

### 只配置一个密钥可以用吗？

可以。至少配置 `SILICONFLOW_API_KEY` 或 `MODELSCOPE_API_KEY` 其中一个即可。两个都配置会更稳。

### Pollinations 缺省关闭，怎么启用？

在 `.env` 中设置 `BUILTIN_PROVIDER_POLLINATIONS_ENABLED=true`。Pollinations 不在默认降级链中，启用后需要在请求中显式指定 `model: "pollinations"` 才会调用。不填 `POLLINATIONS_API_KEY` 时会使用旧公共图片接口，稳定性和可用性不承诺。

### 图片链接打不开怎么办？

检查 `PUBLIC_BASE_URL`。如果 Agent 和网关不在同一台机器，不能用默认的 `http://localhost:9890`，应该改成网关机器的局域网地址，例如：

```env
PUBLIC_BASE_URL=http://192.168.1.10:9890
```

### 端口 9890 被占用了怎么办？

修改：

```env
PROXY_PORT=9891
PUBLIC_BASE_URL=http://你的服务器IP:9891
```

### 魔搭返回 403 / 401 怎么办？

优先检查：

1. `MODELSCOPE_API_KEY` 是否填错；
2. 令牌是否过期；
3. 账号是否能正常访问 API-Inference；
4. 模型是否临时下线或额度已用尽。

### 生成很慢正常吗？

正常。图片生成通常需要几秒到几十秒。魔搭是异步任务，网关会提交任务后轮询结果。

### 怎么查看本地额度保护计数？

访问：

```bash
curl http://localhost:9890/health
```

里面会显示魔搭本地保护计数。注意这不等于平台真实余额，只是本地保护值。

### 可以公网开放吗？

可以，但必须配置 `GATEWAY_API_KEY`，并建议放在 Nginx/Caddy 后面启用 HTTPS、限流和访问日志。

---

## 配置中心与自定义渠道

Web Studio v0.2.0 的 Provider 页面分成两类：

- 自定义渠道：可通过 Web Studio 创建和管理 `openai_image` 类型渠道，供 Generate Image 选择。
- 内置 / catalog / reserved 渠道：以只读 compact/folded 区块展示能力、状态和配置摘要，不在 v0.2.0 Studio 中编辑密钥或 `base_url`。

当前边界：

- 内置渠道密钥、`base_url`、启用状态主要通过 `.env` / 运行时配置管理；不要把 Studio 描述成可编辑所有 builtin key/base_url 的控制台。
- Pollinations 为 experimental/disabled 渠道，缺省关闭，不进入默认可用链。
- Generate Image 已经 catalog-aware：支持默认路由、catalog model 选择、自定义 provider 选择。
- 自定义 provider 的 `provider_model` 是“发给上游服务商的模型名/本次 override”，不是本地 catalog model id；仅在 `model=custom:<provider_id>` 时使用。
- Provider catalog API：`/v1/admin/catalog` 返回所有内置 provider、model、capabilities、params 和 size_presets（需要 admin 登录）。
- 旧管理入口（`/v1/admin/providers/*` 系列 API）仍保留为历史兼容入口，不作为 v0.2.0 主推荐路径。
- 发布建议：公网部署必须配置 `GATEWAY_API_KEY`、后台强密码，并放在 HTTPS 反向代理后。

---

---

## Agnes 图片/视频能力

Agnes 属于显式调用渠道，不进入默认降级链。

- 图片别名：`agnes-image`、`agnes-2.1`、`agnes-2.0`
- 视频入口：`POST /v1/videos`、`GET /v1/videos/{task_id}`
- 图片能力示例：`docs/AGNES_IMAGE_CALL_EXAMPLES.md`
- 视频能力示例：`docs/AGNES_VIDEO_CALL_EXAMPLES.md`
- 总索引：`docs/AGNES_MODEL_CALL_EXAMPLES.md`

如果使用 Agnes，请在 `.env` 中填写：

```env
AGNES_API_KEY=你的 Agnes 密钥
AGNES_BASE_URL=https://apihub.agnes-ai.com/v1
AGNES_IMAGE_MODEL=agnes-image-2.1-flash
```

注意：Agnes 是否免费、额度多少、具体字段名和模型名，以 Agnes 官方文档和后台为准。

## License

MIT


---

## 生成文件本地化

从 v0.1.0 开始，网关默认会把远端临时 URL 下载到本地 `OUTPUT_DIR`，再返回稳定的本地地址：

```text
http://你的服务器:9890/generated/xxx.png
http://你的服务器:9890/generated/xxx.mp4
```

为什么要这样做：

- ModelScope 可能返回 OSS 临时图片链接；
- Agnes 可能返回 GCS 临时视频链接；
- 这些远端链接过一段时间可能失效；
- Agent 更适合拿稳定的本地 URL 或本地文件路径发给用户。

相关环境变量：

```env
AUTO_DOWNLOAD_GENERATED=true
LOCALIZE_STRICT=false
MEDIA_DOWNLOAD_MAX_BYTES=314572800
UPLOAD_MAX_FILES=10
```

图片接口会在生成成功后自动本地化，并写入 Assets。视频接口分两种情况：

- `wait_for_completion=true`：生成完成后立即下载到本地；
- 异步视频任务：提交后返回 `task_id` / `job_id`，没有独立后台进程持续检查完成状态；用户或 Web Studio 调用 `GET /v1/videos/{task_id}` 查询状态时，若任务已完成，网关会尝试下载到本地并写入 Assets。

`/generated/*` 和 `/uploads/*` 都需要认证访问，并支持已认证请求使用 `HEAD` 检查文件是否存在。未登录请求不能直接读取受保护媒体。

---

## 路由 API

除了直接调用生成接口，v0.2.0 提供一个轻量路由辅助接口：

```text
POST /v1/media/route
```

`/v1/media/route` 用于让 Agent 或 UI 在生成前获取模型、尺寸和视频输入模式建议。

提示词整理仍建议由 Agent 或前端交互层完成；v0.2.0 不公开普通提示词增强路由，避免文档宣传不存在的接口。


---

## Web UI

- Studio：`GET /` 或 `GET /studio`
- 管理后台：`GET /admin`
- API 文档：`GET /api-docs`

Studio 包含以下页面：

- Dashboard（仪表盘）
- Account（账号弹窗）：查看当前单管理员账号，修改 username/password；修改时都需要 `current_password`，成功后会清除 session，需要重新登录
- Generate Image（生成图片）：catalog-aware；支持默认路由、catalog model、自定义 provider，以及自定义 provider 的 `provider_model` 上游模型 override
- Generate Video（生成视频）：catalog-aware 最小视频提交页面，从 `/v1/admin/catalog` 获取 video provider/model/capabilities
- Jobs（任务列表）：最小可用列表，显示任务状态和生成结果关联；不宣传完整事件时间线或高级诊断
- Assets（资产库）：最小可用列表，生成文件和上传文件可见；不宣传完整高级管理能力
- Providers（服务商管理）：自定义渠道管理；builtin/catalog/reserved 区域只读 compact/folded 展示
- API Keys（API 模式密钥）

Studio 保持生成工作流干净：生成时会显示基础进度状态，图片完成后在结果区展示；JSON 调试信息默认折叠，需要时展开查看。v0.2.0 不包含完整诊断界面、`job_events` 事件界面，也不包含后台持续轮询视频直到完成的机制。

v0.2.0 的 Ange 小助手目前为 WIP 状态：后台模型配置、模型拉取和连通性测试功能已实现，但小助手的公开生成路由尚未开放，Studio 里的生成规划和”确认并执行”工作流仍属于未来计划。小助手功能默认关闭（`ANGE_ASSISTANT_ENABLED=false`），不作为 v0.2.0 stable 的可用功能。

管理后台的“配置中心”按用途分组展示配置项：

- 基础网关与本地化：网关访问密钥、公开访问地址、生成文件本地化和上传限制；
- 内置渠道（图片生成）：SiliconFlow、ModelScope、Pollinations；Pollinations 为 experimental/disabled，缺省关闭；
- Agnes 图片与视频：Agnes 密钥和接口地址；
- OpenAI-compatible 图片：显式付费图片通道；
- 渠道管理：自定义 OpenAI Images 渠道用于 Studio 选择；builtin/catalog/reserved 能力区只读展示；
- Ange 小助手：OpenAI-compatible LLM 规划器。

普通用户看到的是中文名称和用途说明；环境变量名只作为开发者排查标识显示在字段底部。


---

## 本地运行层

这一版不再只是无状态代理，增加了本地 SQLite 记忆层：

- 本地数据库：`ANGEMEDIA_DB_FILE`
- Provider 配置管理：`/v1/admin/config`
- Provider 配置元数据：`/v1/admin/config-metadata`
- 渠道状态与模板：`/v1/admin/provider-status`、`/v1/admin/provider-templates`
- 自定义渠道排序/测试/启停：`/v1/admin/providers/*`
- 小助手模型拉取与连通性测试：`/v1/admin/assistant/models`、`/v1/admin/assistant/test`
- 生成历史：`/v1/history`
- 视频任务记录：`/v1/video-tasks`
- 多图上传和角色标注：`/v1/uploads`
- 可选 Ange 生图小助手的配置与连通性测试

这不是 Redis/Celery/K8s 后台任务系统，也不提供多租户、SaaS、计费或完整配额产品。旧版数据不会自动导入或 backfill；v0.2.0 新实例应按当前 SQLite 状态结构使用。

Ange 小助手可以用 OpenAI-compatible LLM 接入。如果未开启或未配置，生成工作流应回退到基础路由和前端/Agent 提示词整理。

小助手配置项：

```env
ANGE_ASSISTANT_ENABLED=false
ANGE_LLM_API_KEY=
ANGE_LLM_BASE_URL=https://api.openai.com/v1
ANGE_LLM_MODEL=gpt-4o-mini
ANGE_LLM_TEMPERATURE=0.35
ANGE_LLM_TIMEOUT=60
ANGE_ASSISTANT_ALLOW_PAID=false
ANGE_ASSISTANT_ALLOW_AGNES=true
ANGE_ASSISTANT_CONFIRM_PLAN=false
```

管理后台入口：

```text
/admin
```

Studio 入口：

```text
/
```


---

## 模块化后端结构

兼容启动入口仍然保留：

```text
scripts/proxy.py
```

真实实现已经迁移到：

```text
scripts/angemedia_gateway/
```

现在配置、配置元数据、SQLite 状态库、请求模型、媒体本地化、模型路由、Ange 小助手、Provider、FastAPI 路由装配都分开维护。`server.py` 只负责应用装配；页面、管理、媒体、文件/历史路由放在 `scripts/angemedia_gateway/routes/`。后续新增能力应该放进对应模块，不要堆回启动入口。


---

## 独立 Agent Skill 包

给 Agent 使用的技能已经单独拆到：

```text
skill/
```

这样 Agent 只需要读取生图/生视频调用规则，不会被 Web 管理后台、开发文档、前端说明干扰。

CI 发布时会同时打包：

```text
angemedia-gateway-<version>.zip
angemedia-gateway-skill-<version>.zip
```

完整项目给开发者使用；`skill/` 包给 Agent 安装使用。


---

## 管理后台登录

管理后台默认启用账号密码登录：

```text
账号：admin
密码：ADMIN_DEFAULT_PASSWORD 的值
```

首次启动前必须显式设置 `ADMIN_DEFAULT_PASSWORD`。首次启动时会把密码保存为 PBKDF2 哈希，不会明文落库。生产环境请第一次登录后立刻修改密码。

可通过环境变量修改初始值：

```env
ADMIN_USERNAME=admin
ADMIN_DEFAULT_PASSWORD=换成一个足够长的随机密码
ADMIN_COOKIE_SECURE=false
```


---

## Docker 部署与镜像发布

Dockerfile 会复制 `app/` 目录，因此容器内可以正常访问 Studio、管理后台和 API 文档页面。生产容器内端口是 `8000`，compose 默认把宿主机 `9892` 映射到容器 `8000`。

GitHub Actions 的 DockerHub workflow 不再随普通 `main` push 运行。pull request 仅在 Docker 相关文件变化时构建且不推送；`v*` tag 发布对应版本标签，不带预发布后缀的稳定 tag 同时发布 `latest`。手动运行默认只构建，明确启用 `push_image` 输入后才发布 `edge`。

启用 DockerHub 发布前需要在 GitHub 仓库配置：

- Secrets：`DOCKERHUB_USERNAME`、`DOCKERHUB_TOKEN`
- Variable：`DOCKERHUB_REPOSITORY`，填写完整 DockerHub 镜像名，例如 `dockerhub-user/angemedia-gateway`


---

## 安全补充

- 管理后台登录有基础限速：连续失败 5 次会锁定 30 秒。
- 网关密钥生成后默认只返回预览，不再把完整 key 自动写入 localStorage。
- Docker 镜像内置 HEALTHCHECK。
- 管理接口使用 HttpOnly Cookie 登录。Gateway API Key 可用于生成、Jobs、受保护媒体等 API 模式入口，但不能访问 `/v1/admin/account`、`/v1/admin/username`、`/v1/admin/password` 等管理账号 API。

## 安全提示：GATEWAY_API_KEY

如果没有配置 `GATEWAY_API_KEY`，AngeMedia 会允许本机或局域网客户端直接调用图片/视频生成 API。这是为了方便内网和单机部署。

公网部署必须配置 `GATEWAY_API_KEY`，并建议放在 HTTPS 反向代理之后，同时设置管理后台强密码。

- `UPLOAD_MAX_FILES` 控制 `/v1/uploads` 单次最多上传文件数，默认 10。远端媒体本地化下载会校验初始 URL 和每次重定向目标，降低 SSRF 风险。
