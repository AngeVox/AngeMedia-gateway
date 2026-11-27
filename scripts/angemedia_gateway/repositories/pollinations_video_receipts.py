"""记录唯一一次 Pollinations 提交的身份；收据只包含本地摘要和上游编号。"""


def video_receipt(conn, job_id):
    row = conn.execute("SELECT * FROM pollinations_video_receipts WHERE job_id=?", (job_id,)).fetchone()
    return dict(row) if row is not None else None


def reserve_video_submit(conn, job_id, intent, connection):
    conn.execute(
        "INSERT INTO pollinations_video_receipts(job_id,intent_digest,connection_digest,state) VALUES(?,?,?,'prepared')",
        (job_id, intent, connection),
    )


def receive_video(conn, job_id, task_id, retry_after=0):
    state = "accepted" if task_id else "cached"
    updated = conn.execute(
        "UPDATE pollinations_video_receipts SET state=?, task_id=?, retry_after=? WHERE job_id=? AND state='prepared'",
        (state, task_id, retry_after, job_id),
    )
    if updated.rowcount != 1:
        raise RuntimeError("Pollinations video receipt has already been recorded")
