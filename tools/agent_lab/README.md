# 助手运行时验证工具

在隔离环境中验证助手所用的 LangChain、LangGraph 和异步 SQLite 检查点。这些工具供贡献者复现会话、工具调用和恢复行为。

验证在独立环境执行。依赖已并入根目录正式锁，实验锁仅引用它，避免版本重复维护；不要直接升级正在运行的部署环境。

```sh
python3.12 -m venv /path/to/isolated-agent-env
/path/to/isolated-agent-env/bin/python -m pip install -r tools/agent_lab/requirements.lock
/path/to/isolated-agent-env/bin/python tools/agent_lab/engine_lab.py --root /path/to/new-evidence-directory
```

每次使用新的证据目录，保留前次失败记录。脚本会启动只监听本机的临时HTTP接口，走真实SDK与Agent执行链，验证多轮工具、参数和未知工具、非法JSON、拒答/空正文/输出预算、HTTP错误/超时、取消、会话隔离，以及工具前后暂停后的跨进程恢复和终态重放。受控接口不代表真实模型验证。

真实模型验证另用`live_engine.py`：仅在拥有现有助手配置的机器运行，参数指向配置文件和SQLite数据库；数据库以只读方式打开。凭据仅在进程内使用，不导出、不写入报告，不启用远程追踪。工具只查询随机生成的本地测试数据，不读取或修改应用业务记录。

```sh
python tools/agent_lab/live_engine.py --root /path/to/new-live-evidence --phase pause --runtime-env /path/to/app.env --runtime-db /path/to/app.db
python tools/agent_lab/live_engine.py --root /path/to/new-live-evidence --phase resume --runtime-env /path/to/app.env --runtime-db /path/to/app.db
python tools/agent_lab/live_engine.py --root /path/to/new-live-evidence --phase replay --runtime-env /path/to/app.env --runtime-db /path/to/app.db
```

三个命令分别启动进程。第一步工具完成后暂停；第二步从检查点读取模型上下文并执行依赖第一步结果的工具；第三步验证终态重放不请求模型或重复工具。每阶段模型调用最多3次，SDK重试关闭；失败时保留证据并调查，不能把重跑当作自动恢复策略。

正式接入还需要应用层运行记录、会话租约、认证、工具允许列表、上下文与检查点脱敏、保留策略、SSE事件映射，以及业务动作的持久幂等映射。SQLite检查点成功不等于任意业务动作不会重复执行。此处的ProtocolGuard仅用于协议分类实验，不能替代应用权限或脱敏边界。

`runtime_probe.py --root /path/to/new-runtime-evidence --baseline-python /path/to/base/python`另测依赖增量、新进程导入和并发边界。RSS/HWM读取当前进程的/proc数据，不以可能继承父进程峰值的rusage直接作内存差值。

目录位于tools，当前Docker与fnOS正式构建仅复制app/scripts等运行文件，实验工具与其专用依赖不会进入服务包。

检查点租约桥接位于`scripts/angemedia_gateway/assistant_checkpoints.py`，已由应用生命周期持有的运行服务接入；以下命令用于在独立依赖环境复核桥接合同：

```sh
python -m unittest discover -s tools/agent_lab -p 'test_*.py' -v
```

该组使用真实框架与SQLite，覆盖接管后旧进程写入、提交中重复取消、初始化争用、回滚故障、typed消息和图恢复，不请求模型。运行库与检查点库必须是独立的本地文件，thread_id绑定run_id；适配器只提供异步读写，管理性删除/复制/裁剪不向运行执行者开放。它不承担心跳、后台恢复扫描、业务动作幂等或检查点保留策略，相关集成需独立验收。框架依赖使用项目根目录的锁文件。

`test_supervisor_graph.py`另外验证运行控制与真实图的联合合同：关闭后恢复、进程强退后从已提交节点继续、检查点提交期间总体超时，以及三个进程同时争抢一个运行只执行一次。纯运行控制测试在`tests/test_assistant_supervisor.py`，默认应用测试环境即可运行。后台控制器已接入应用生命周期；本文件的独立只读节点测试只证明运行控制合同，媒体动作幂等另由动作确认、worker和作品测试验证。

`test_application_engine.py`验证应用内核适配器：真实SDK/HTTP、多步工具、严格参数、持久预算、历史投影、错误分类、脱敏、检查点载荷限额及独立保留清理。`live_application_engine.py`在授权机器复用既有助手配置，测试随机数据的两步依赖工具；`--phase run`执行、`--phase replay`在另一进程验证终态重放不发请求。凭据在本进程读取，报告只保存计数与验证结果，不导出配置。两阶段共用新的`--root`目录，并显式传入`--runtime-env`和`--runtime-db`，只读现有配置/库，不写生产状态。

应用内核与检查点保留函数已经可单独使用，但尚未在服务启动中启用。正式依赖、CI、Docker及fnOS已统一使用根锁；fnOS双架构从同一锁下载，核对目标标签、元数据、依赖闭包与哈希。最低Python为3.11，fnOS使用3.12；ARM64轮子校验不等于ARM64实机验收。统一聊天入口另行接线，不提供静默回退到关键词流程的内核模式。
