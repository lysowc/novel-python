"""定时任务（在 worker 进程里每分钟跑一次；多个 worker 用 Redis 锁保证同一分钟只有一个在跑）

- 定时续写：到点后为开启了定时的小说安排连续续写（每本每天一次）
- 回收站清理：每天一次，彻底删除超过保留天数的小说
"""
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.db import SessionLocal
from app.models import Novel, NovelAutomation
from app.services import budget, novel_trash, task_service

TICK_SECONDS = 60
LOCK_KEY = "ai:scheduler:lock"


def local_now() -> datetime:
    try:
        return datetime.now(ZoneInfo(settings.timezone or "Asia/Shanghai"))
    except Exception:  # noqa: BLE001  时区写错时退回服务器时间
        return datetime.now()


def run_schedules(db, current: datetime) -> list[str]:
    """检查所有定时续写，返回本次的处理说明（便于日志和测试）"""
    today, hhmm = current.date(), current.strftime("%H:%M")
    notes = []
    rows = db.query(NovelAutomation).filter(NovelAutomation.schedule_enabled == 1).all()
    for auto in rows:
        if auto.last_run_date == today or hhmm < (auto.schedule_time or "08:00"):
            continue
        novel = db.query(Novel).filter(Novel.id == auto.novel_id).first()
        if not novel or novel.deleted_at is not None:
            auto.schedule_enabled = 0
            auto.last_run_message = "小说已删除，定时续写已关闭"
            continue
        reason = budget.block_reason(db, novel.id)
        if reason:
            auto.last_run_date = today
            auto.last_run_message = f"{today} 跳过：{reason}"
            notes.append(f"《{novel.title}》{auto.last_run_message}")
            continue
        count = max(1, int(auto.chapters_per_run or 1))
        params: dict = {"remaining": count - 1}
        if auto.target_words:
            params["target_words"] = int(auto.target_words)
        try:
            task = task_service.create(db, "continue_chapter", novel.id, params)
        except RuntimeError as e:
            # 例如这本小说正有任务在跑：不记今天已执行，下一分钟再试
            auto.last_run_message = f"{hhmm} 等待：{e}"
            notes.append(f"《{novel.title}》{auto.last_run_message}")
            continue
        auto.last_run_date = today
        auto.last_run_message = f"{today} {hhmm} 已安排续写 {count} 章（任务 #{task.id}）"
        notes.append(f"《{novel.title}》{auto.last_run_message}")
    db.commit()
    return notes


def tick() -> None:
    r = task_service.connect_redis()
    try:
        if not r.set(LOCK_KEY, "1", nx=True, ex=TICK_SECONDS - 5):
            return  # 其他 worker 这一分钟已经跑过
        current = local_now()
        db = SessionLocal()
        try:
            for note in run_schedules(db, current):
                print("[scheduler]", note)
            requeued = task_service.requeue_stale_pending(db)
            if requeued:
                print(f"[scheduler] {requeued} 个排队过久且不在队列里的任务已重新入队")
            if r.set(f"ai:trash:purged:{current.date()}", "1", nx=True, ex=2 * 86400):
                purged = novel_trash.purge_expired(db)
                if purged:
                    print(f"[scheduler] 回收站自动清理 {purged} 本小说")
        finally:
            db.close()
    finally:
        r.close()


def start_background() -> threading.Thread:
    def loop() -> None:
        while True:
            try:
                tick()
            except Exception as e:  # noqa: BLE001
                print("[scheduler] error:", e)
            time.sleep(TICK_SECONDS)

    thread = threading.Thread(target=loop, name="scheduler", daemon=True)
    thread.start()
    return thread

