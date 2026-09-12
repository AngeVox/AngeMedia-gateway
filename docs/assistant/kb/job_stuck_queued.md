# Job Stuck Queued Runbook

任务长期停在 queued 时，先区分“没有被 dispatcher 取走”和“已经发布但 worker 没消费”。查看安全 Job 摘要里的 dispatch 状态统计、latest dispatch error、attempt_count，以及 Diagnostics 的 queue health 和 active_total。

如果没有 worker attempt，重点检查 dispatcher 和当前 queue backend；如果 dispatch 已 published 但没有 attempt，可能是消息没有到达 worker。AngeMedia 的 SQLite job/job_dispatches 是持久化事实来源，不应通过删除数据库记录来“解卡”。

如果已有 running attempt，则它已经不是纯 queued 问题，应按当前 stage 排查 Provider、poll 或 asset import。任何恢复动作都应避免对可能已经提交给 Provider 的生成任务进行盲目重复提交。