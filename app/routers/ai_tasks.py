"""AI 任务接口：CRUD + SSE 任务流订阅（与 PHP 版契约一致）"""
import json
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import fail, json_dumps, ok
from app.models import AiTask
from app.routers.admin import f_task
from app.services import task_service

router = APIRouter(prefix="/api/admin/ai/tasks", tags=["ai-tasks"])

PARAMS_ALLOWED = {"chapter_no", "target_words", "instruction", "remaining"}


@router.get("")
def index(page: int = Query(1), page_size: int = Query(10), novel_id: int = Query(0),
          db: Session = Depends(get_db)):
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    q = db.query(AiTask)
    if novel_id > 0:
        q = q.filter(AiTask.ref_id == novel_id, AiTask.ref_type == "novel")
    total = q.count()
    rows = q.order_by(AiTask.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return ok({"list": [f_task(t) for t in rows], "total": total, "page": page, "page_size": page_size})


@router.post("")
def store(payload: dict, db: Session = Depends(get_db)):
    type_ = str(payload.get("task_type") or "")
    novel_id = int(payload.get("novel_id") or 0)
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    params = {k: v for k, v in params.items() if k in PARAMS_ALLOWED}
    try:
        task = task_service.create(db, type_, novel_id, params)
    except RuntimeError as e:
        return fail(str(e))
    return ok(f_task(task), "任务已创建")


@router.get("/{task_id}")
def show(task_id: int, db: Session = Depends(get_db)):
    task = db.query(AiTask).filter(AiTask.id == task_id).first()
    if not task:
        return fail("任务不存在")
    return ok(f_task(task))


@router.post("/{task_id}/retry")
def retry_task(task_id: int, db: Session = Depends(get_db)):
    try:
        task = task_service.retry(db, task_id)
    except RuntimeError as e:
        return fail(str(e))
    return ok(f_task(task), "已重新入队")


@router.get("/{task_id}/stream")
def stream(task_id: int, db: Session = Depends(get_db)):
    """订阅任务流（SSE）：阻塞式轮询 Redis 流缓冲，任务结束或超时即退出"""
    task = db.query(AiTask).filter(AiTask.id == task_id).first()
    if not task:
        return fail("任务不存在")

    def generate():
        from app.services.task_service import connect_redis, STREAM_PREFIX

        # 任务已终态：直接发完成事件
        if task.status in ("success", "failed"):
            yield "data: " + json_dumps({
                "type": "done", "status": task.status, "error_message": task.error_message,
            }) + "\n\n"
            return

        started_at = time.time()
        last_event_at = started_at
        while True:
            events = []
            try:
                r = connect_redis()
                try:
                    while True:
                        raw = r.rpop(STREAM_PREFIX + str(task_id))
                        if raw is None:
                            break
                        events.append(raw)
                        if len(events) >= 300:
                            break
                finally:
                    r.close()
            except Exception:
                pass  # 连接失败下轮重试

            done = False
            for raw in events:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                yield "data: " + json_dumps(event) + "\n\n"
                last_event_at = time.time()
                if event.get("type") == "done":
                    done = True
            if done:
                break

            # 兜底：任务已终态但事件缺失（如 worker 崩溃），静默 30s 后补发完成事件
            try:
                fresh = db.query(AiTask).filter(AiTask.id == task_id).first()
            except Exception:
                fresh = None
            if fresh and fresh.status in ("success", "failed") and time.time() - last_event_at > 30:
                yield "data: " + json_dumps({
                    "type": "done", "status": fresh.status, "error_message": fresh.error_message,
                }) + "\n\n"
                break

            # 安全上限：30 分钟
            if time.time() - started_at > 1800:
                yield "data: " + json_dumps({
                    "type": "done", "status": "failed", "error_message": "流订阅超时",
                }) + "\n\n"
                break

            time.sleep(0.3)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
