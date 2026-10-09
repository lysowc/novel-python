"""AI 任务消费进程（独立进程，阻塞式消费 Redis 队列）

启动方式：
    .venv/bin/python -m app.worker
"""
import sys
import time

from app.services.task_service import QUEUE_KEY, connect_redis, execute


def main() -> None:
    print("[worker] AI 任务消费进程已启动，队列:", QUEUE_KEY)
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
