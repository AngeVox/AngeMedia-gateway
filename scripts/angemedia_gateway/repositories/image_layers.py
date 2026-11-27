"""分层取回收据和已发布层组的持久状态。"""
import json
import re
from contextlib import closing
from pathlib import Path

from .. import config as C
from ..db.connection import db_connect, db_transaction
from ..job_submission import canonical_submission_json
from ..layer_paths import LAYER_NAMESPACE, is_layer_relative_path


def layer_receipt(conn, job_id):
    row = conn.execute("SELECT * FROM image_layer_receipts WHERE job_id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def prepare_layer_receipt(conn, job_id, digest, deadline):
    conn.execute("INSERT INTO image_layer_receipts(job_id,intent_digest,state,deadline) VALUES(?,?,'prepared',?)",
                 (job_id, digest, deadline))


def accept_layer_receipt(conn, job_id, entries, expires):
    conn.execute("UPDATE image_layer_receipts SET state='received', deadline=?, result_json=? WHERE job_id=?",
                 (expires, canonical_submission_json(entries), job_id))


def close_layer_receipt(conn, job_id, *, manifest=None):
    conn.execute("UPDATE image_layer_receipts SET state=?, result_json=NULL, manifest_json=? WHERE job_id=?",
                 ("failed" if manifest is None else "published",
                  None if manifest is None else canonical_submission_json(manifest), job_id))


def layer_manifest(job_id):
    with closing(db_connect()) as conn:
        receipt = layer_receipt(conn, job_id)
    return json.loads(receipt["manifest_json"]) if receipt and receipt["state"] == "published" else None


def remove_layer_group(job_id: str) -> bool:
    """撤下清单和成员引用，历史任务可继续独立保留。"""
    with db_transaction(immediate=True) as conn:
        receipt = layer_receipt(conn, job_id)
        if receipt is None or receipt["state"] not in {"published", "removed"}:
            return False
        if receipt["state"] == "published":
            prefix = f"{LAYER_NAMESPACE}/{job_id}/"
            conn.execute("DELETE FROM assets WHERE storage_area='output' AND substr(relative_path,1,?)=?",
                         (len(prefix), prefix))
            conn.execute("UPDATE image_layer_receipts SET state='removed', manifest_json=NULL WHERE job_id=?",
                         (job_id,))
    return True


def layer_asset_group(asset: dict) -> dict | None:
    """从成员路径取得稳定组标识，任务记录清理后仍可定位层组。"""
    if asset.get("storage_area") != "output":
        return None
    path = Path(asset.get("relative_path") or "").as_posix()
    member = re.fullmatch(rf"{re.escape(LAYER_NAMESPACE)}/([a-f0-9]{{32}})/([0-9]{{3}})\.(?:png|jpg)", path)
    if not member:
        return None
    return {"job_id": member[1], "z_index": int(member[2])}


def generated_file_visible(path: Path) -> bool:
    """普通输出走原路径，分层成员须已与整个清单一起提交。"""
    relative = path.relative_to(C.OUTPUT_DIR.resolve()).as_posix()
    if not is_layer_relative_path(relative):
        return True
    parts = relative.split("/")
    if len(parts) != 3:
        return False
    with closing(db_connect()) as conn:
        return conn.execute(
            "SELECT 1 FROM assets WHERE storage_area='output' AND relative_path=? "
            "AND EXISTS(SELECT 1 FROM image_layer_receipts WHERE job_id=? AND state='published')",
            (relative, parts[1]),
        ).fetchone() is not None
