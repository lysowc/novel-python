"""AI 任务消费进程（独立进程，阻塞式消费 Redis 队列）

启动方式：
    .venv/bin/python -m app.worker
"""
import sys
import time

from app.services.task_service import DEFER_DELAY, QUEUE_KEY, clear_novel_locks, connect_redis, execute


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

    # 所有 worker 都在重启，不可能有任务在执行：清掉残留的小说锁，免得等 TTL 过期
    try:
        cleared = clear_novel_locks()
        if cleared:
            print(f"[worker] 已清除 {cleared} 个残留的小说锁")
    except Exception as e:
        print("[worker] 清除小说锁失败:", e)


def main() -> None:
    # --recover：只做一次卡死任务恢复后退出（由 scripts/start.sh 在启动多进程前调用）
    if "--recover" in sys.argv:
        recover_stuck_tasks()
        return
    print("[worker] AI 任务消费进程已启动，队列:", QUEUE_KEY)
    # 定时续写 / 回收站清理（多个 worker 都启动，靠 Redis 锁保证同一时间只有一个在跑）
    from app.services import scheduler
    scheduler.start_background()
    while True:
        task_id = None
        try:
            r = connect_redis()
            try:
                # 入队用 LPUSH，这里从右端取 → 先进先出（此前 BLPOP 是后进先出，任务多时最早的会一直排不到）
                item = r.brpop(QUEUE_KEY, timeout=30)
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
            if not execute(task_id):
                # 该小说有其他任务在执行，任务已放回队尾；稍等再取，避免空转
                time.sleep(DEFER_DELAY)
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
