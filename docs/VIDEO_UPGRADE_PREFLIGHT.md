# 视频任务升级前盘点

本工具只检查视频任务。需要同时盘点图片、Agent、投递与schema时，使用[应用升级只读总览](UPGRADE_PREFLIGHT.md)的包内模块入口；两个工具都不授权部署，原视频报告合同保持。

在仓库根目录使用项目Python环境执行：

```powershell
python tools/video_upgrade_preflight.py --database "D:/path/to/angemedia.db"
```

也可在Linux使用同一脚本和明确的数据库绝对路径。工具不读取默认应用数据库、不初始化或迁移目标库，不更改任务、配置或来源收据，不调用服务方。它通过SQLite `mode=ro` 和一个读事务盘点；SQLite可能需要访问WAL/SHM辅助文件，运行用户需要相应的读取和锁访问能力。**不要复制只有主数据库文件、漏掉未checkpoint的WAL来做上线判断。** 工具没有使用会忽略WAL的`immutable`模式。

在提供 POSIX 有效 UID 的环境里，主库或已有 WAL、SHM 的属主与盘点账号不一致时，工具在打开 SQLite 前返回退出码 1 和 `preflight_database_owner_mismatch`。在线盘点使用实际应用运行账号；跨账号使用同一停写点取得的冻结副本。[应用升级只读总览](UPGRADE_PREFLIGHT.md)

报告只输出JSON，不含提示词、原始响应、密钥、端点、明文外部任务号或本地文件路径。正常job ID保留以便定位；不合规ID和外部编号仅显示摘要。明细默认最多1000项，可用`--detail-limit 0`只看汇总，或设置0至10000；阻塞项优先显示。上限只影响明细，计数和结论仍扫描全部记录，`details_omitted`明确标注省略量。

| 退出码 | 含义 |
|---|---|
| 0 | 本次数据库快照中没有发现已记录的任务证据阻塞，`task_gate=no_recorded_blockers` |
| 2 | 有待处理的任务、来源或身份冲突，`task_gate=blocked` |
| 1 | 无法完整盘点，包括文件不存在、表结构不兼容或参数越界；只返回固定错误，不输出异常正文 |

报告的`deployment_ready`始终为false。退出码0不能证明入口和worker已停止接收工作、当前连接仍为原账户、远端任务已结束、备份可恢复或安装包兼容。真正执行时仍经过原授权和连接门槛，盘点结果不是执行凭据。

主要分类：

| 原因码 | 处理依据 |
|---|---|
| `pending_first_submission` | 尚无开始/尝试证据的队列首次提交；未来执行时再记录实际来源，不给历史补来源 |
| `original_connection_required` | 已有相符执行收据或关联助手原授权；能否继续仍由真实执行入口核验当前连接、政策和输入 |
| `unverified_queued_task` / `unverified_legacy_task` | 已有外部任务但缺原来源证据；升级前在原环境盘点/收尾，不能凭当前配置自动认领 |
| `submission_outcome_unknown` / `submission_unresolved` | 有开始/尝试但没有可靠提交结果；不能自动重提，也不能当成尚未执行 |
| `recorded_submission_rejection` | 已失败的旧任务中，同一快照留有明确的原提交拒绝证据。保留记录；该项不阻塞盘点，也不授予重提或恢复权限 |
| `existing_task_unresolved` | 本地失败不代表服务方结束；保留原任务及证据，单独核实远端结果 |
| `invalid_*` | 原授权、执行摘要或载荷不一致；修复前核对证据，不能自动刷新签名或回填来源 |
| `ambiguous_external_identity` | 同号匹配多个video job；与兼容GET的409边界一致，不按时间挑选 |
| `cached_only_terminal_record` | 只有本地终态记录，不证明仍有job或原账户已验证；作品文件可用性另行核对 |
| `cached_only_unresolved_task` | 只有缓存且服务方结果仍不明确，需要升级前核实 |
| `orphaned_unbound_attempt` | job已清理但保留无任务号的尝试收据；不能由摘要恢复任务号或推断未收费 |

生产执行顺序应包含停止新入口、按原版本/原配置核实存量、备份与恢复演练、完整盘点、升级及回退验证。只读盘点不负责停止进程、执行迁移或解决历史账户归属。旧同步API仍有独立返回合同与参考媒体/扩展字段能力，本工具没有切换它的执行路径。
