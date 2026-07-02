# AngeMedia Gateway

[English](README.md) | [简体中文](README_CN.md)

> 当前版本目标：v0.2.1。AngeMedia Gateway 是面向 AI Agent、New-API、NAS 和自托管工作流的图片/视频生成网关，提供 OpenAI-compatible 图片接口、异步视频任务、队列 worker、Web Studio 和本地资产管理。

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-ready-009688.svg)](https://fastapi.tiangolo.com/)

## 核心能力

- OpenAI-compatible 图片生成：`POST /v1/images/generations`
- 异步视频任务：`POST /v1/videos`、`GET /v1/videos/{task_id}`
- SQLite 作为任务真相源：jobs、events、attempts、dispatches、request dedupe
- Redis/Celery 作为队列 broker，dispatcher 和 worker 走正式 outbox/worker runtime
- Web Studio：Dashboard、生成图片、生成视频、任务、资产、渠道、API 密钥、诊断、小助手
- Jobs Task Center：服务端分页/过滤、安全详情、事件、尝试、诊断、关联资产
- Dashboard / Assets 与 queued job 摘要打通
- Prompt Copilot 和 AngeMedia 小助手，支持共享 LLM 设置与安全本地 fallback
- 远端临时媒体服务端本地化到受控 `/generated/*` 或 `/uploads/*`

## 架构

```text
AI Agent / New-API / OpenAI SDK / AngeMedia Studio
        |
        v
AngeMedia Gateway API
        |
        v
SQLite jobs + job_events + job_attempts + job_dispatches
        |
        v
Dispatcher -> Redis/Celery -> Worker runtime
        |
        v
图片 / 视频渠道适配器
        |
        v
/generated 或 /uploads 本地资产
```

Redis 只做 broker，不保存业务真相。业务状态以 SQLite 为准。

## 快速开始

```bash
git clone https://github.com/AngeVox/angemedia-gateway.git
cd angemedia-gateway

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export ADMIN_USERNAME=admin
export ADMIN_DEFAULT_PASSWORD='换成足够长的随机密码'
python -m uvicorn scripts.angemedia_gateway.server:app --host 127.0.0.1 --port 9890
```

打开：

```text
http://localhost:9890/studio
```

健康检查：

```bash
curl http://localhost:9890/health
```

## 队列运行时

真实 queued generation 需要 API、Redis、dispatcher、worker 同时运行。

```bash
docker compose up -d redis
python -m uvicorn scripts.angemedia_gateway.server:app --host 127.0.0.1 --port 9890
python -m angemedia_gateway.cli.dispatcher
python -m angemedia_gateway.cli.worker --loglevel INFO
```

Compose smoke gate 默认不调用真实 provider：

```bash
python scripts/ci/queue_compose_smoke.py --dry-run
```

Docker 可用时可以运行真实 compose smoke：

```bash
python scripts/ci/queue_compose_smoke.py
```

## 配置

只配置你实际要用的渠道。Provider key 不会写入镜像。

```env
ADMIN_USERNAME=admin
ADMIN_DEFAULT_PASSWORD=换成足够长的随机密码
GATEWAY_API_KEY=换成足够长的随机网关密钥

SILICONFLOW_API_KEY=
MODELSCOPE_API_KEY=
POLLINATIONS_API_KEY=
AGNES_API_KEY=

OPENAI_IMAGE_API_KEY=
OPENAI_IMAGE_BASE_URL=https://api.openai.com/v1
OPENAI_IMAGE_MODEL=gpt-image-2

ANGE_LLM_ENABLED=false
ANGE_LLM_BASE_URL=
ANGE_LLM_API_KEY=
ANGE_LLM_MODEL=

IMAGE_PROVIDER_TIMEOUT=300
VIDEO_PROVIDER_TIMEOUT=900
```

`ANGE_LLM_MODEL` 不再内置 `gpt-4o` 之类占位值。请在 Studio 小助手设置里通过当前 API 地址拉取模型，或手动填写实际可用模型。

## Docker Compose

生产容器监听 `8000`，仓库内 compose 默认映射为 `9892:8000`。

```bash
export ADMIN_USERNAME=admin
export ADMIN_DEFAULT_PASSWORD='换成足够长的随机密码'
export GATEWAY_API_KEY='换成足够长的随机网关密钥'
docker compose up -d --build
```

访问：

```text
http://localhost:9892/studio
```

运行数据通过命名卷持久化到容器内 `/app/state`、`/app/generated`、`/app/uploads`。

## API 示例

生成图片：

```bash
curl -X POST http://localhost:9890/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"prompt":"一只戴墨镜的猫，赛博朋克风格，霓虹灯背景","size":"1024x1024"}'
```

提交视频任务：

```bash
curl -X POST http://localhost:9890/v1/videos \
  -H "Content-Type: application/json" \
  -d '{"prompt":"A cinematic shot of a cat walking through a neon rainy street","num_frames":121,"frame_rate":24}'
```

生成前路由：

```bash
curl -X POST http://localhost:9890/v1/media/route \
  -H "Content-Type: application/json" \
  -d '{"prompt":"画一张现实风格的美女写真"}'
```

如果配置了 `GATEWAY_API_KEY`，API-mode 请求需要带：

```http
Authorization: Bearer <GATEWAY_API_KEY>
```

Gateway API Key 不能访问 Admin Session API。

## Web Studio

入口：

```text
GET /studio
GET /
```

主要页面：

- Dashboard：队列状态、最近任务、最近失败、最近资产、存储摘要
- 生成图片：渠道、模型、尺寸、Prompt Copilot、queued submission、结果预览
- 生成视频：文生视频、图生视频、queued submission、结果预览
- 任务：服务端分页/过滤、安全详情、事件、尝试、诊断、关联资产
- 资产：本地生成/上传资产、任务/生成记录摘要
- 渠道：图片/视频分类、运行时配置、连接测试
- 诊断：运行时、队列、媒体目录、失败任务的安全诊断
- 小助手：AngeMedia 范围内问答、排障、共享 LLM 设置、本地 fallback
- API 密钥：创建和撤销 API-mode key

Studio 是静态 vanilla JavaScript，不引入 React、Vite、TypeScript 或构建工具。

## 渠道

内置图片渠道包括 SiliconFlow、ModelScope、Pollinations、OpenAI-compatible Image、ByteDance Seedream、Agnes Image。是否可用取决于运行时配置和 catalog 状态。

当前发布版视频能力以 Agnes Video 为主。代码里可以保留后续视频渠道 registry/internal 预留，但未完整验收的渠道不会出现在默认生成 UI 中。

UI 使用“渠道”这个术语；后端字段仍保留历史 `provider` 命名，避免无意义迁移。

## 小助手与 Prompt Copilot

AngeMedia 小助手只回答 AngeMedia Gateway / Studio / 队列 / 生成 / 渠道 / 资产 / 诊断相关问题。启用 LLM 后会调用 OpenAI-compatible 接口；LLM 不可用时安全回退到本地知识库和规则建议。

Prompt Copilot 可在生成图片和生成视频页使用。它返回中文说明和英文模型提示词。中文用户界面显示中文，但发给生成模型的提示词默认是英文；英文用户不会被强制中文化。

小助手和 Prompt Copilot 不展示 API key、Authorization、raw provider body、request hash、signed URL、data URL 或本地 filesystem path。

## 安全边界

- Admin API 使用 HttpOnly Admin Session。
- Gateway API Key 只用于 API-mode 生成和受保护媒体访问。
- `/generated/*` 和 `/uploads/*` 是受控媒体路径，需要认证访问。
- Provider signed URL 只在服务端下载本地化，不返回给 UI。
- Jobs/Dashboard/Assets 只返回安全摘要，不返回 raw `input_json`、`output_json`、request hash 或 provider raw body。
- 本地 filesystem path 不进入公开接口或 Studio 摘要。
- 真实 provider key 只能通过本地运行配置提供，不应写进代码、测试、文档或日志。

## Agent Skill

Agent-facing skill 文件位于：

```text
skill/
```

它只描述图片/视频生成调用、路由和提示词规则，不包含完整 Web Studio 或开发文档，避免污染 Agent 上下文。

关键文档：

- 主入口：`SKILL.md`
- 图片生成：`docs/SKILL_IMAGE_GENERATION.md`
- 视频生成：`docs/SKILL_VIDEO_GENERATION.md`
- 路由：`docs/SKILL_MEDIA_ROUTING.md`
- 提示词：`docs/SKILL_PROMPT_ENHANCEMENT.md`
- 小助手输出 schema：`docs/ANGE_ASSISTANT_OUTPUT_SCHEMA.md`

## 发布打包

发布流程应生成代码包和 skill 包：

```text
angemedia-gateway-<version>.zip
angemedia-gateway-skill-<version>.zip
```

不要把本地运行目录、生成媒体、SQLite 数据库、日志、`.env`、`.codex`、`.agent`、`.pytest_cache`、`.playwright-output` 或 `output/` 打进发布包。

## 兼容入口

旧兼容入口保留：

```text
scripts/proxy.py
```

真实后端实现位于：

```text
scripts/angemedia_gateway/
```

## License

Apache-2.0 License。
