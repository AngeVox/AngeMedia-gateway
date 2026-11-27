# 应用升级只读总览

## 从 v0.2.13 升级

1. 停止接收新任务，让原版本用原渠道配置收尾。停写后，备份数据库、仍需参与读取的 WAL、配置、素材及独立的助手检查点（如有）。保留原备份。
2. 用另一份副本演练。把候选版的数据、素材、队列和端口指向隔离环境，禁止外连，再启动一次，让应用完成数据库迁移。
3. 停止隔离实例，使用下方命令盘点迁移后的主库，并用测试账号验登录、任务和素材读取。直接用新版命令扫描未迁移的 v0.2.13 库时，会报告缺少新版的表和迁移记录。遇到这个结果，先确认盘点目标和演练步骤。
4. 复核仍未收尾的任务和备份恢复结果，再切换正式环境。若需回退，用升级前的完整备份恢复旧版。

## Upgrade checklist

1. Stop accepting new work and let the old deployment finish in-flight jobs using their original provider settings. Stop writes and back up the database, any WAL still needed for reads, settings, media, and any separate assistant checkpoint database. Keep this backup intact.
2. Rehearse on a separate copy. Point the candidate at isolated data, media, queues, and ports. Disable outbound access and start it once to apply schema migrations.
3. Stop the isolated instance and run the command below on its migrated database. Check sign-in, jobs, and media with a test account. The v0.2.15 inventory reports the missing v0.2.15 tables and migration records on a v0.2.13 database before migration.
4. Review unresolved jobs and verify that you can restore the backup before upgrading the live deployment. To roll back, restore the full pre-upgrade backup with the old version.

## 盘点命令 / Inventory command

使用已安装项目依赖的Python。仓库环境先把`scripts`加入`PYTHONPATH`，然后使用模块入口：

Use Python with the project dependencies installed. From a repository checkout, add its `scripts` directory to `PYTHONPATH` and run the module:

```sh
PYTHONPATH=/path/to/repository/scripts python -m angemedia_gateway.cli.upgrade_preflight --database /absolute/path/angemedia.db --detail-limit 100
```

PowerShell：

```powershell
$env:PYTHONPATH = 'D:/path/to/repository/scripts'
python -m angemedia_gateway.cli.upgrade_preflight --database 'D:/absolute/path/angemedia.db' --detail-limit 100
```

模块随`scripts`进入FPK和Docker运行文件。已安装环境使用自身解释器与包路径执行同一模块，不需要另装CLI依赖。必须显式指定主库；不会读取默认应用数据库或配置文件，不初始化/迁移目标库，不创建缺失目录，不访问渠道、代理或检查点正文，不修复/认领/重签任务，也不停止进程。

盘点使用`mode=ro`、`query_only`和一个主库读事务，包含schema、原视频分类、图片收据、Agent运行/租约/提案、attempt和outbox。它复用既有视频分类，因此原[视频CLI](VIDEO_UPGRADE_PREFLIGHT.md)的范围、输出与退出码保留。WAL会参与当前快照；不要使用漏WAL的主文件拷贝或`immutable`模式来推断生产存量。

在提供 POSIX 有效 UID 的环境里，CLI 会在 SQLite 连接前核对主库与已有 WAL、SHM 文件的属主。与当前有效 UID 不一致时返回退出码 1 和 `preflight_database_owner_mismatch`。SQLite 可以为 `mode=ro` 的 WAL 库创建辅助文件。运行中盘点使用实际服务账号；跨账号盘点先停止相关写入者，再交付同一时点、由盘点账号拥有的冻结副本，保留仍需参与读取的 WAL。[SQLite WAL 只读连接规则](https://sqlite.org/wal.html#readonly)

| 退出码 | 含义 |
|---|---|
| 0 | 本次主库快照未发现已记录阻塞；不是部署许可 |
| 2 | 有活动工作、未知/不完整证据或明确的盘点阻塞 |
| 1 | 无法完整读取，包括文件不存在、现存表缺必要列或读取错误；只输出固定错误JSON，不回传路径/异常正文 |

`deployment_ready`始终为false。`inventory_gate`表示盘点结果；`drain_observation`分别为`recorded_work_present`、`unverified`或`no_recorded_work`，最后一个也不证明入口已关闭、broker已清空或所有进程都退出。缺失已知证据表会列为阻塞，其对应计数为null；有表且没有记录才是0。未知迁移名仅输出摘要。旧库不自动升级，字段不兼容会明确失败。

明细上限0..10000只限制输出，不截断扫描与计数；跨领域合并后阻塞项仍优先。`blocker_count`计数的是发现项，同一任务可能涉及多个原因，不能当成待处理任务数。正常32位任务/运行ID保留定位，其他标识摘要化；报告不含提示词、端点、凭据、绑定密钥、租约token、输入/输出正文、原始外部编号或文件路径。

主要边界：

- 活动图片/视频、活动Agent和未释放租约均列为工作；过期租约不会由盘点回收。摘要相符的未确认提案仅记录为保留信息，不入队，不把它当正在生成，也不据此保证仍可确认。
- 图片prepared/uncertain收据、缺原来源、摘要/编号不符、未收尾终态与孤立不确定收据均阻塞。相符收据也不证明当前连接仍为原账户，执行时继续走原门槛。
- 已完成任务的有效迟到守护投递可以作为保留事实；publishing以及活动任务的pending投递仍视为可能有活动。未知/损坏或跨媒体投递合同不能混成正常守护消息。broker本身不在只读SQLite盘点范围内。
- 完成图片的保留收据与旧终态记录不自动变成生成许可；盘点不读取作品文件来证明其可用性。原视频缓存/来源/同号冲突分类原样复用。
- 旧版通用失败码下已留有明确提交拒绝证据的终态任务，报告为 `image_recorded_submission_rejection` 或 `video.recorded_submission_rejection`。判定使用同一主库快照中的单次提交、匹配的失败事件和已知拒绝响应，缺少证据、存在后续阶段、受理或写入迹象时继续阻塞。原任务状态、出处、重提授权保持不变。审阅账本可另行投影，其存在与否不改变此判定。

主DB、独立Agent检查点、配置和媒体的整体一致备份、实际路径映射、原生平台、旧客户端、真实账户及升级/回退仍须分别验收。该CLI不证明目标旧版本认识当前schema/载荷/阶段，不能据此恢复旧包消费新任务。
