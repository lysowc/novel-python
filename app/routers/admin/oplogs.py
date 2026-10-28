"""后台 API：操作记录"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import dt_str, ok
from app.models import OperationLog

router = APIRouter(prefix="/api/admin", tags=["admin"])


def f_oplog(o: OperationLog) -> dict:
    return {
        "id": o.id, "admin_id": o.admin_id, "username": o.username, "action": o.action,
        "summary": o.summary, "method": o.method, "path": o.path,
        "target_type": o.target_type, "target_id": o.target_id,
        "success": o.success, "message": o.message, "ip": o.ip, "created_at": dt_str(o.created_at),
    }


@router.get("/operation-logs")
def operation_logs(page: int = Query(1), page_size: int = Query(20), keyword: str = Query(""),
                   failed_only: int = Query(0), novel_id: int = Query(0), db: Session = Depends(get_db)):
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    q = db.query(OperationLog)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(OperationLog.action.like(like) | OperationLog.summary.like(like)
                     | OperationLog.username.like(like) | OperationLog.ip.like(like))
    if failed_only:
        q = q.filter(OperationLog.success == 0)
    if novel_id:
        q = q.filter(OperationLog.target_type == "novel", OperationLog.target_id == novel_id)
    total = q.count()
    rows = q.order_by(OperationLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return ok({"list": [f_oplog(o) for o in rows], "total": total, "page": page, "page_size": page_size})
