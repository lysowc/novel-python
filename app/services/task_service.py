"""AI 任务队列：Redis List + 独立 worker 进程消费（与 PHP 版协议一致）"""
import json

import redis
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.helpers import json_dumps, now
from app.models import AiTask, Novel

QUEUE_KEY = "ai:queue"
STREAM_PREFIX = "ai:stream:"
STREAM_TTL = 3600

TYPES = [
    "generate_setting",
    "generate_outline",
    "generate_chapter",
    "continue_chapter",
    "regenerate_chapter",
    "generate_summary",
    "update_memory",
    "consistency_check",
]


def connect_redis() -> redis.Redis:
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_database,
        password=settings.redis_password or None,
        socket_connect_timeout=5,
        decode_responses=True,
    )


def _push_task(task_id: int) -> None:
    r = connect_redis()
    try:
        r.lpush(QUEUE_KEY, str(task_id))
    finally:
        r.close()


def publish(task_id: int, event: dict) -> None:
    try:
        r = connect_redis()
        try:
            key = STREAM_PREFIX + str(task_id)
            r.lpush(key, json_dumps(event))
            r.expire(key, STREAM_TTL)
        finally:
            r.close()
    except Exception:
        pass  # 发布失败不影响任务本身


def finish(task_id: int, status: str, error: str = "") -> None:
    publish(task_id, {"type": "done", "status": status, "error_message": error[:500]})
    try:
        r = connect_redis()
        try:
            r.setex(STREAM_PREFIX + str(task_id) + ":done", STREAM_TTL, status)
        finally:
            r.close()
    except Exception:
        pass


def create(db: Session, type_: str, novel_id: int, params: dict | None = None) -> AiTask:
    """创建任务并入队（同一小说同时只允许一个进行中任务）"""
    if type_ not in TYPES:
        raise RuntimeError("未知任务类型: " + type_)
    if not db.query(Novel).filter(Novel.id == novel_id).first():
        raise RuntimeError("小说不存在")
    running = (
        db.query(AiTask)
        .filter(AiTask.ref_id == novel_id, AiTask.ref_type == "novel",
                AiTask.status.in_(["pending", "running"]))
        .first()
    )
    if running:
        raise RuntimeError(f"该小说已有进行中的任务（{running.task_type}），请等待完成")
    return enqueue(db, type_, novel_id, params)


def enqueue(db: Session, type_: str, novel_id: int, params: dict | None = None) -> AiTask:
    """内部自动入队（不检查同小说进行中任务，用于章节完成后自动审校）"""
    if type_ not in TYPES:
        raise RuntimeError("未知任务类型: " + type_)
    task = AiTask(
        task_type=type_,
        ref_id=novel_id,
        ref_type="novel",
        params=params or {},
        status="pending",
        error_message="",
        created_at=now(),
        updated_at=now(),
    )
    db.add(task)
    db.commit()
    _push_task(task.id)
    publish(task.id, {"type": "status", "status": "pending", "message": "任务已入队"})
    return task


def retry(db: Session, task_id: int) -> AiTask:
    task = db.query(AiTask).filter(AiTask.id == task_id).first()
    if not task:
        raise RuntimeError("任务不存在")
    if task.status != "failed":
        raise RuntimeError("只有失败的任务才能重试")
    running = (
        db.query(AiTask)
        .filter(AiTask.ref_id == task.ref_id, AiTask.ref_type == task.ref_type,
                AiTask.status.in_(["pending", "running"]))
        .first()
    )
    if running:
        raise RuntimeError("该小说已有进行中的任务，请等待完成")
    task.status = "pending"
    task.error_message = ""
    task.updated_at = now()
    db.commit()
    _push_task(task.id)
    publish(task.id, {"type": "status", "status": "pending", "message": "任务已重新入队"})
    return task


def execute(task_id: int) -> None:
    """由 worker 消费：执行一个任务（独立 DB 会话）"""
    from app.services.ai_service import AiService

    db = SessionLocal()
    try:
        task = db.query(AiTask).filter(AiTask.id == task_id).first()
        if not task or task.status == "success":
            return

        task.status = "running"
        task.error_message = ""
        task.updated_at = now()
        db.commit()
        publish(task_id, {"type": "status", "status": "running", "message": "任务开始执行"})

        try:
            novel = db.query(Novel).filter(Novel.id == task.ref_id).first()
            if not novel:
                raise RuntimeError("小说不存在")

            ai = AiService(db)

            def on_stage(stage: str, message: str = "") -> None:
                publish(task_id, {"type": "status", "stage": stage, "message": message})

            def on_delta(delta: str) -> None:
                publish(task_id, {"type": "chunk", "content": delta})

            if task.task_type == "generate_setting":
                ai.generate_setting(novel, on_stage)
            elif task.task_type == "generate_outline":
                ai.generate_outline(novel, on_stage)
            elif task.task_type in ("generate_chapter", "continue_chapter", "regenerate_chapter"):
                ai.generate_chapter(novel, task, on_delta, on_stage)
            elif task.task_type == "generate_summary":
                ai.generate_summary_for_chapter(novel, task, on_stage)
            elif task.task_type == "update_memory":
                ai.update_memory(novel)
                on_stage("done", "小说记忆更新完成")
            elif task.task_type == "consistency_check":
                ai.check_consistency(novel, task, on_stage)
            else:
                raise RuntimeError("未知任务类型: " + task.task_type)

            task.status = "success"
            task.error_message = ""
            task.updated_at = now()
            db.commit()
            finish(task_id, "success")
        except Exception as e:
            db.rollback()
            message = str(e)
            task = db.query(AiTask).filter(AiTask.id == task_id).first()
            if task:
                task.status = "failed"
                task.error_message = message[:900]
                task.updated_at = now()
                db.commit()
            finish(task_id, "failed", message)
    finally:
        db.close()
