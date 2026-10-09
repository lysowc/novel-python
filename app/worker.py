"""AI 任务消费进程（独立进程，阻塞式消费 Redis 队列）

启动方式：
    .venv/bin/python -m app.worker
"""
import sys
import time

from app.services.task_service import QUEUE_KEY, connect_redis, execute


def recover_stuck_tasks() -> None:
    """启动时恢复：把残留的 running 任务标记为 failed。

    进程被 kill/重启时，正在执行的任务会永久卡在 running；重启后没有任何任务
    真正在执行，因此把所有 running 都视为已中断，改为 failed 供用户重试。
    """
    from app.db import SessionLocal
    from app.helpers import now
    from app.models import AiTask

    db = SessionLocal()
    try:
        stuck = db.query(AiTask).filter(AiTask.status == "running").all()
        for task in stuck:
            task.status = "failed"
            task.error_message = "任务进程重启，执行被中断（可重试）"
            task.updated_at = now()
        if stuck:
            db.commit()
            print(f"[worker] 已将 {len(stuck)} 个中断任务恢复为 failed（可重试）")
    finally:
        db.close()


def main() -> None:
    print("[worker] AI 任务消费进程已启动，队列:", QUEUE_KEY)
    recover_stuck_tasks()
    while True:
        task_id = None
        try:
            r = connect_redis()
            try:
                item = r.blpop(QUEUE_KEY, timeout=30)
            finally:
                r.close()
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                task_id = int(item[1])
        except Exception as e:
            print("[worker] redis error:", e)
            time.sleep(5)
            continue

        if not task_id:
            continue

        try:
            execute(task_id)
        except Exception as e:
            print("[worker] execute error:", e)
            # 兜底：标记失败，避免任务永远 running
            from app.db import SessionLocal
            from app.helpers import now
            from app.models import AiTask
            from app.services.task_service import finish
            db = SessionLocal()
            try:
                task = db.query(AiTask).filter(AiTask.id == task_id).first()
                if task and task.status == "running":
                    task.status = "failed"
                    task.error_message = str(e)[:900]
                    task.updated_at = now()
                    db.commit()
                    finish(task_id, "failed", str(e))
            except Exception:
                pass
            finally:
                db.close()


if __name__ == "__main__":
    sys.exit(main())
