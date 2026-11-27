# 管理 Cookie 客户端与页面升级协议

管理 Cookie 代表当前浏览器会话。登录状态可能被另一标签改变，因此客户端必须同时携带自己已经确认的 `session_context`，不能只依赖 Cookie。

1. 调用 `POST /v1/admin/login`，或用已有 Cookie 调用 `GET /v1/admin/session`。
2. 登录成功响应包含 `session_context`；session 响应仅在 `authenticated=true` 时包含它。
3. 受保护的 Cookie 请求携带 `X-Studio-Session-Context`。上传和流式读取也遵守此规则。
4. 原生 EventSource 无法设置请求头，可通过 `studio_session_context` 查询参数携带同一标记。
5. 退出也携带原标记；不要在重试前把它换成另一会话的标记。

```python
# 密码从调用方安全输入获取；此例只读取身份，不创建业务任务。
with httpx.Client(base_url=base_url) as client:
    login = client.post('/v1/admin/login', json={'username': username, 'password': password})
    login.raise_for_status()
    headers = {'X-Studio-Session-Context': login.json()['session_context']}
    identity = client.get('/v1/admin/me', headers=headers)
    identity.raise_for_status()
```

标记由会话令牌以独立域派生，不是登录凭证；标记单独不能授权访问，也不能代替 Cookie。不要把密码、Cookie 令牌放入查询参数或共享存储。

| 响应码 | 机器码 | 含义与处理 |
|---|---|---|
| 409 | `session_context_required` | Cookie 请求缺少标记。旧页面需刷新，外部 Cookie 客户端需接入上述协议。 |
| 409 | `session_context_changed` | 实际 Cookie 与客户端原标记不同。不要自动重试写入，应重新确认登录身份。 |
| 409 | `studio_upgrade_required` | 新版页面声明的资源版本与服务端不同。保存要保留的文字后，由用户明确刷新。 |
| 401 | 原认证错误 | 会话无效或过期，沿原登录恢复流程处理。 |

新版页面自动从所加载模块的版本路径携带 `X-Studio-Revision`；EventSource 使用 `studio_revision`。普通 Cookie SDK 不需要声明页面资源版本，但必须携带会话标记。网关 API Key 的普通业务鉴权方式保持原合同；网关 Key 不能取得管理权限。

前端 HTML 不缓存，引用按内容版本区分的完整资源图。版本化资源可长期缓存，未版本化路径按内容 ETag 重新校验。服务进程使用固定资源快照；更新前端文件后应重启服务。仅切换查询串或设置 no-cache 不能替换已打开页面的模块。

运行中的页面不自动刷新，不自动重发已发出的业务操作。兼容检查不等于升级、备份或回退验收；发布仍需核对实际安装包和目标平台。

## 打包与运行接入

fnOS构建在调用fnpack之前记录待打包的应用载荷、控制文件和manifest；最终FPK解包后逐项核对文件集合与SHA256。只允许fnpack添加归档checksum字段，遗漏资源、替换同数量轮子或修改启动脚本都会失败。验包成功后才复制到输出目录并生成校验文件。这个检查证明包与暂存输入一致，不代表输入代码已通过业务验收或包已通过原生安装。

部署时先停止应用，再替换完整程序和前端资源，随后启动新进程。直接覆盖磁盘文件不会更新运行中进程的资源快照。可比较首页响应的`X-Studio-Revision`、HTML资源路径及资源响应中的版本，确认同一来源；该内容版本不等于Git提交号。应避免同时将不同版本进程放入同一代理后端。

Web Studio目前以域名根路径提供`/studio`、`/assets`及`/v1`。反向代理需保留这些路径、Cookie、`X-Studio-Session-Context`和`X-Studio-Revision`请求头、SSE查询参数，以及服务端的Cache-Control/ETag响应；不要缓存登录、会话或业务API。仅版本化静态资源可按服务端策略长期缓存。子路径重写、平台应用中心停启、ARM和回退仍需各自验收，不能由直连或普通代理测试推定通过。
