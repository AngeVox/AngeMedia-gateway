# ModelScope Slow Poll Runbook

ModelScope 异步图片任务可能长时间保持 RUNNING，超过短轮询窗口并不必然表示任务失败。当前 AngeMedia 默认 ModelScope 轮询窗口按较长异步任务设计，排障时应查看 task status、已等待时间和最终 error category，而不是在 1-2 分钟后立即重提。

如果 submit 成功并已经获得 task id，应继续查询同一个任务。不要因为前几次 poll 都是 RUNNING 就生成第二个任务。只有 Provider 返回明确 failed/error 状态，或达到有界总超时后，才进入失败处理。

若大量任务同时异常变慢，应考虑 Provider 侧排队、额度或服务状态，并结合 429/5xx 诊断，而不是单独提高客户端并发。