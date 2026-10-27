"""AI 任务接口：CRUD + SSE 任务流订阅（与 PHP 版契约一致）"""
import asyncio
import json
import time

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.helpers import fail, json_dumps, ok
from app.models import AiTask
from app.routers.admin import f_task
from app.services import task_service

router = APIRouter(prefix="/api/admin/ai/tasks", tags=["ai-tasks"])

TERMINAL = ("success", "failed", "cancelled")

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


@router.post("/{task_id}/cancel")
def cancel_task(task_id: int, payload: dict | None = None, db: Session = Depends(get_db)):
    """取消任务；body {"after_current": true} 表示连续续写写完当前这章后停止"""
    after_current = bool((payload or {}).get("after_current"))
    try:
        task = task_service.cancel(db, task_id, after_current)
    except RuntimeError as e:
        return fail(str(e))
    msg = "已取消" if task.status == "cancelled" else (
        "将在写完本章后停止连续续写" if after_current else "已请求取消，任务会在下一个检查点中止")
    return ok(f_task(task), msg)


@router.get("/{task_id}/stream")
def stream(task_id: int, db: Session = Depends(get_db)):
    """订阅任务流（SSE）：增量读取 Redis 流缓冲，任务结束或超时即退出

    - 异步生成器：等待期间不占用线程池线程，也不长期占用数据库连接
    - 只读取新增事件（LLEN + 区间 LRANGE），不再每轮把整条列表读一遍
    - 断开重连从头回放（游标从 0 开始），所以能看到已生成的全部内容
    """
    task = db.query(AiTask).filter(AiTask.id == task_id).first()
    if not task:
        return fail("任务不存在")
    status, error_message = task.status, task.error_message

    async def generate():
        # 任务已终态：直接发完成事件
        if status in TERMINAL:
            yield "data: " + json_dumps({"type": "done", "status": status, "error_message": error_message}) + "\n\n"
            return

        key = task_service.STREAM_PREFIX + str(task_id)
        r = task_service.connect_redis_async()
        started_at = last_event_at = last_check_at = last_ping_at = time.time()
        cursor = 0  # 已推送的事件数
        try:
            while True:
                try:
                    length = await r.llen(key)
                    if length < cursor:  # 流被清空（任务重试），从头读
                        cursor = 0
                    # lpush 是头插：最新事件在下标 0，未读的是 [0, length - cursor - 1]，反转成时间正序
                    raw_events = list(reversed(await r.lrange(key, 0, length - cursor - 1))) if length > cursor else []
                except Exception:
                    raw_events = []  # 连接失败下轮重试

                for raw in raw_events:
                    cursor += 1
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    yield "data: " + json_dumps(event) + "\n\n"
                    last_event_at = last_ping_at = time.time()
                    if event.get("type") == "done":
                        return

                now_ts = time.time()
                # 兜底：任务已终态但完成事件缺失（如 worker 崩溃）——每 3 秒用新会话查一次真实状态
                if now_ts - last_check_at >= 3 and now_ts - last_event_at > 30:
                    last_check_at = now_ts
                    fresh = await asyncio.to_thread(_task_status, task_id)
                    if fresh and fresh[0] in TERMINAL:
                        yield "data: " + json_dumps({
                            "type": "done", "status": fresh[0], "error_message": fresh[1],
                        }) + "\n\n"
                        return

                # 安全上限：30 分钟
                if now_ts - started_at > 1800:
                    yield "data: " + json_dumps({
                        "type": "done", "status": "failed", "error_message": "流订阅超时",
                    }) + "\n\n"
                    return

                # 心跳：排队等待时长时间没有事件，避免代理/浏览器断开空闲连接
                if now_ts - last_ping_at >= 15:
                    last_ping_at = now_ts
                    yield ": ping\n\n"

                await asyncio.sleep(0.3)
        finally:
            try:
                await r.aclose()
            except Exception:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _task_status(task_id: int) -> tuple[str, str] | None:
    """用独立的短会话读取任务最新状态（不受请求会话的缓存/事务快照影响）"""
    session = SessionLocal()
    try:
        row = session.query(AiTask.status, AiTask.error_message).filter(AiTask.id == task_id).first()
        return (row[0], row[1]) if row else None
    finally:
        session.close()
