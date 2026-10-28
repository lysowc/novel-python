"""AI 任务队列：Redis List + 独立 worker 进程消费（与 PHP 版协议一致）"""
import threading
import time
import uuid
from contextlib import contextmanager

import redis
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.helpers import json_dumps, now
from app.models import AiTask, Novel
from app.services import budget
from app.services.ai_client import AiFatalError, CallbackAbort

QUEUE_KEY = settings.ai_queue_key or "ai:queue"
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
    "rebuild_memory",
    "extend_outline",
]


# ---------- 小说级互斥锁（同一本小说的任务串行执行） ----------

LOCK_PREFIX = "ai:lock:novel:"
LOCK_TTL = 600            # 秒；持锁期间由心跳线程续期，worker 异常退出后最多 10 分钟自动释放
LOCK_RENEW_INTERVAL = 60  # 心跳续期间隔（秒）
DEFER_DELAY = 1.0         # 拿不到锁放回队列后的等待（秒），避免空转
MAX_CHAIN_FAILURES = 3    # 连续续写中连续失败多少章后停止

_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) "
    "else return 0 end"
)
_RENEW_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) "
    "else return 0 end"
)


class NovelLock:
    """基于 Redis SET NX 的小说锁，带心跳续期；只有持有者（token 匹配）才能续期/释放"""

    def __init__(self, novel_id: int):
        self.key = LOCK_PREFIX + str(novel_id)
        self.token = uuid.uuid4().hex
        self._redis: redis.Redis | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def acquire(self) -> bool:
        self._redis = connect_redis()
        if not self._redis.set(self.key, self.token, nx=True, ex=LOCK_TTL):
            self._redis.close()
            self._redis = None
            return False
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._thread.start()
        return True

    def _heartbeat(self) -> None:
        while not self._stop.wait(LOCK_RENEW_INTERVAL):
            try:
                if not self._redis.eval(_RENEW_LUA, 1, self.key, self.token, LOCK_TTL):
                    print(f"[worker] 警告：锁 {self.key} 已丢失")
                    return
            except Exception as e:
                print(f"[worker] 锁续期失败 {self.key}: {e}")

    def release(self) -> None:
        if self._redis is None:
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        try:
            self._redis.eval(_RELEASE_LUA, 1, self.key, self.token)
        except Exception as e:
            print(f"[worker] 释放锁失败 {self.key}: {e}")
        finally:
            self._redis.close()
            self._redis = None


def clear_novel_locks() -> int:
    """清除全部小说锁（仅在 worker 全部重启前调用，此时不可能有任务在执行）"""
    r = connect_redis()
    try:
        keys = list(r.scan_iter(LOCK_PREFIX + "*"))
        return r.delete(*keys) if keys else 0
    finally:
        r.close()


def connect_redis() -> redis.Redis:
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_database,
        password=settings.redis_password or None,
        socket_connect_timeout=5,
        decode_responses=True,
    )


def connect_redis_async():
    """异步 Redis 客户端（SSE 订阅用，等待时不占线程）"""
    import redis.asyncio as aioredis
    return aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_database,
        password=settings.redis_password or None,
        socket_connect_timeout=5,
        decode_responses=True,
    )


def novel_busy(db: Session, novel_id: int) -> bool:
    """该小说是否有排队中/执行中的任务"""
    return db.query(AiTask.id).filter(
        AiTask.ref_id == novel_id, AiTask.ref_type == "novel",
        AiTask.status.in_(["pending", "running"]),
    ).first() is not None


@contextmanager
def novel_guard(db: Session, novel_id: int, action: str = "修改"):
    """API 侧修改小说内容（删除/恢复版本等）时使用：有任务排队/执行就拒绝，并在操作期间持有小说锁，
    保证 worker 不会同时开始这本小说的任务。拒绝时抛 RuntimeError（消息可直接展示给用户）。"""
    if novel_busy(db, novel_id):
        raise RuntimeError(f"该小说有 AI 任务正在排队或执行，请等任务结束后再{action}")
    lock = NovelLock(novel_id)
    try:
        acquired = lock.acquire()
    except Exception:
        raise RuntimeError(f"任务队列（Redis）不可用，暂时无法确认是否有任务在执行，请稍后再{action}")
    if not acquired:
        raise RuntimeError(f"该小说有 AI 任务正在执行，请稍后再{action}")
    try:
        yield
    finally:
        lock.release()


# ---------- 取消 ----------

CANCEL_PREFIX = "ai:cancel:"        # 立即取消执行中的任务
STOP_CHAIN_PREFIX = "ai:stopchain:"  # 连续续写：写完当前这章后停止
CANCEL_CHECK_INTERVAL = 1.0


class TaskCancelled(CallbackAbort):
    """用户取消了正在执行的任务"""


def cancel(db: Session, task_id: int, after_current: bool = False) -> AiTask:
    """取消任务。

    - 排队中：直接标记为已取消（worker 领取时会跳过）
    - 执行中：after_current=False → 发出取消信号，worker 在下一个检查点（流式输出每秒检查）中止；
              after_current=True → 只对连续续写有效：写完当前这章后不再安排下一章
    """
    task = db.query(AiTask).filter(AiTask.id == task_id).first()
    if not task:
        raise RuntimeError("任务不存在")
    if task.status == "pending":
        updated = (
            db.query(AiTask).filter(AiTask.id == task_id, AiTask.status == "pending")
            .update({"status": "cancelled", "error_message": "已取消", "updated_at": now()},
                    synchronize_session=False)
        )
        db.commit()
        if updated:
            finish(task_id, "cancelled", "已取消")
            db.refresh(task)
            return task
        db.refresh(task)  # 恰好被 worker 领取，按执行中处理
    if task.status != "running":
        raise RuntimeError("任务已结束，无需取消")
    r = connect_redis()
    try:
        key = (STOP_CHAIN_PREFIX if after_current else CANCEL_PREFIX) + str(task_id)
        r.setex(key, STREAM_TTL, "1")
    finally:
        r.close()
    publish(task_id, {
        "type": "status", "stage": "cancel_requested",
        "message": "已请求停止：写完本章后不再续写" if after_current else "已请求取消，正在中止…",
    })
    return task


def _flag(prefix: str, task_id: int) -> bool:
    try:
        r = connect_redis()
        try:
            return bool(r.exists(prefix + str(task_id)))
        finally:
            r.close()
    except Exception:
        return False


def _clear_flags(task_id: int) -> None:
    try:
        r = connect_redis()
        try:
            r.delete(CANCEL_PREFIX + str(task_id), STOP_CHAIN_PREFIX + str(task_id))
        finally:
            r.close()
    except Exception:
        pass


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


def _clear_stream(task_id: int) -> None:
    try:
        r = connect_redis()
        try:
            r.delete(STREAM_PREFIX + str(task_id), STREAM_PREFIX + str(task_id) + ":done")
        finally:
            r.close()
    except Exception:
        pass


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
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    if not novel:
        raise RuntimeError("小说不存在")
    if novel.deleted_at is not None:
        raise RuntimeError("这本小说在回收站里，恢复后才能使用 AI")
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
    if task.status not in ("failed", "cancelled"):
        raise RuntimeError("只有失败或已取消的任务才能重试")
    running = (
        db.query(AiTask)
        .filter(AiTask.ref_id == task.ref_id, AiTask.ref_type == task.ref_type,
                AiTask.status.in_(["pending", "running"]))
        .first()
    )
    if running:
        raise RuntimeError("该小说已有进行中的任务，请等待完成")

    # 连续续写：失败时链条已经自动安排了下一章，重试只补这一章，不能再从这里重新开一条链；
    # 只有链条是因为错误被"停止"的，重试才接着把剩下的章节写完
    params = dict(task.params or {})
    if task.task_type == "continue_chapter":
        if params.pop("chain_stopped", False):
            params["fail_streak"] = 0
        else:
            params["remaining"] = 0
    task.params = params

    task.status = "pending"
    task.error_message = ""
    task.updated_at = now()
    db.commit()
    _clear_stream(task.id)  # 清掉上一次的进度事件，否则进度框会先回放出旧的"失败"
    _clear_flags(task.id)
    _push_task(task.id)
    publish(task.id, {"type": "status", "status": "pending", "message": "任务已重新入队"})
    return task


def _defer(task_id: int) -> None:
    """拿不到小说锁时把任务放回队尾，稍后再试（任务保持 pending）"""
    r = connect_redis()
    try:
        r.lpush(QUEUE_KEY, str(task_id))  # worker 从右端取（先进先出），lpush 放到队尾
    finally:
        r.close()


def execute(task_id: int) -> bool:
    """由 worker 消费：执行一个任务（独立 DB 会话）

    返回 False 表示该小说正有其他任务在执行，本任务已放回队列等待；其余情况返回 True。
    """
    from app.services.ai_client import is_fatal
    from app.services.ai_service import AiService

    db = SessionLocal()
    try:
        task = db.query(AiTask).filter(AiTask.id == task_id).first()
        # 只执行 pending 任务：重复入队、已在执行/已完成/已失败的残留消息直接丢弃
        if not task or task.status != "pending":
            return True

        # 1. 同一本小说同一时间只允许一个任务执行（不同小说可并行）
        lock = NovelLock(task.ref_id)
        if not lock.acquire():
            _defer(task_id)
            return False

        try:
            # 2. 原子领取：只有把 pending 改成 running 成功的 worker 才执行，杜绝重复执行
            claimed = (
                db.query(AiTask)
                .filter(AiTask.id == task_id, AiTask.status == "pending")
                .update({"status": "running", "error_message": "", "updated_at": now()},
                        synchronize_session=False)
            )
            db.commit()
            if not claimed:
                return True
            task = db.query(AiTask).filter(AiTask.id == task_id).first()
            publish(task_id, {"type": "status", "status": "running", "message": "任务开始执行"})

            error: Exception | None = None
            try:
                novel = db.query(Novel).filter(Novel.id == task.ref_id).first()
                if not novel:
                    raise RuntimeError("小说不存在")
                if novel.deleted_at is not None:
                    raise AiFatalError("这本小说已移入回收站，任务不再执行")
                budget.ensure(db, novel.id)  # 超出费用上限就不再发起 AI 调用

                ai = AiService(db)
                ai.client.novel_id = novel.id  # AI 日志按小说统计用量

                last_check = [0.0]

                def check_cancel(force: bool = False) -> None:
                    now_ts = time.time()
                    if force or now_ts - last_check[0] >= CANCEL_CHECK_INTERVAL:
                        last_check[0] = now_ts
                        if _flag(CANCEL_PREFIX, task_id):
                            raise TaskCancelled()

                def on_stage(stage: str, message: str = "") -> None:
                    check_cancel(force=True)
                    if stage == "reset_output":  # 正文要重写：通知前端清空已显示的内容
                        publish(task_id, {"type": "reset"})
                        return
                    publish(task_id, {"type": "status", "stage": stage, "message": message})

                def on_delta(delta: str) -> None:
                    check_cancel()
                    publish(task_id, {"type": "chunk", "content": delta})

                ai.client.cancel_check = check_cancel  # 非流式调用（摘要/记忆/审校等）等待期间也能取消

                if task.task_type == "generate_setting":
                    ai.generate_setting(novel, on_stage)
                elif task.task_type == "generate_outline":
                    ai.generate_outline(novel, on_stage)
                elif task.task_type in ("generate_chapter", "continue_chapter", "regenerate_chapter"):
                    ai.generate_chapter(novel, task, on_delta, on_stage)
                elif task.task_type == "generate_summary":
                    ai.generate_summary_for_chapter(novel, task, on_stage)
                elif task.task_type == "update_memory":
                    # 可指定章号：用某一章（如刚恢复/手动修改过的章节）重新计算这一章的记忆
                    target = None
                    target_no = int((task.params or {}).get("chapter_no") or 0)
                    if target_no:
                        from app.models import Chapter
                        target = db.query(Chapter).filter(
                            Chapter.novel_id == novel.id, Chapter.chapter_no == target_no).first()
                        if not target:
                            raise RuntimeError(f"第{target_no}章不存在")
                    warning = ai.update_memory(novel, target)
                    on_stage("done", "小说记忆更新完成" + (f"（{warning}）" if warning else ""))
                elif task.task_type == "rebuild_memory":
                    start_no = int((task.params or {}).get("chapter_no") or 1)
                    ai.rebuild_memory(novel, start_no, on_stage)
                elif task.task_type == "extend_outline":
                    ai.extend_outline(novel, int((task.params or {}).get("chapters") or 10), on_stage)
                elif task.task_type == "consistency_check":
                    ai.check_consistency(novel, task, on_stage)
                else:
                    raise RuntimeError("未知任务类型: " + task.task_type)

                task.status = "success"
                task.error_message = ""
                task.updated_at = now()
                db.commit()
            except Exception as e:
                error = e
                db.rollback()
                task = db.query(AiTask).filter(AiTask.id == task_id).first()

            cancelled = isinstance(error, TaskCancelled) or _is_cancelled(error)
            if cancelled and task is not None:
                task.status = "cancelled"
                task.error_message = "已取消" + (
                    f"（第{task.params['resume']['chapter_no']}章正文已保存，摘要/记忆未完成，可重试补做）"
                    if isinstance((task.params or {}).get("resume"), dict) else "")
                task.updated_at = now()
                db.commit()
                finish(task_id, "cancelled", task.error_message)
                _clear_flags(task_id)
                return True

            # 3. 连续续写：可恢复的失败跳过该章继续；配置/鉴权类错误或连续多章失败则停止
            stop_note = ""
            if task is not None:
                try:
                    stop_note = _continue_chain(db, task, error, is_fatal(error))
                    db.commit()  # 续链可能更新了 params（chain_stopped）
                except Exception as e:
                    print("[worker] 续链失败:", e)

            if error is not None:
                message = str(error) + stop_note
                if task is not None:
                    task.status = "failed"
                    task.error_message = message[:900]
                    task.updated_at = now()
                    db.commit()
                finish(task_id, "failed", message)
            else:
                finish(task_id, "success")
            _clear_flags(task_id)
            return True
        finally:
            lock.release()
    finally:
        db.close()


def _is_cancelled(exc: BaseException | None) -> bool:
    """取消信号可能被业务代码包装成 RuntimeError（raise ... from e），沿异常链查找"""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, TaskCancelled):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def _continue_chain(db: Session, task: AiTask, error: Exception | None, fatal: bool) -> str:
    """连续续写续链：remaining > 0 时入队下一章。

    - 成功，或普通失败（超时/限流/返回格式不对等）：跳过并继续下一章
    - 不可恢复错误（未配置 AI、Key 错误、余额不足、模型不存在…）：停止
    - 连续 MAX_CHAIN_FAILURES 章失败：停止，避免空耗请求
    返回停止原因（追加到任务错误信息），没有停止时返回空串。
    """
    if task.task_type != "continue_chapter":
        return ""
    params = task.params or {}
    remaining = int(params.get("remaining") or 0)
    if remaining <= 0:
        return ""
    if _flag(STOP_CHAIN_PREFIX, task.id):
        publish(task.id, {"type": "status", "stage": "auto_continue_stopped",
                          "message": f"已按要求停止连续续写（后续 {remaining} 章未生成）"})
        task.params = {**params, "chain_stopped": True}
        return "" if error is None else f"（已按要求停止连续续写，后续 {remaining} 章未生成）"

    fail_streak = int(params.get("fail_streak") or 0) + 1 if error is not None else 0
    reason = ""
    if fatal:
        reason = "AI 配置或账号有问题"
    elif fail_streak >= MAX_CHAIN_FAILURES:
        reason = f"已连续 {fail_streak} 章失败"
    if reason:
        note = f"（{reason}，连续续写已停止，后续 {remaining} 章未生成，修复后重试本任务可继续）"
        task.params = {**params, "chain_stopped": True}
        publish(task.id, {"type": "status", "stage": "auto_continue_stopped", "message": note.strip("（）")})
        return note

    next_params: dict = {"remaining": remaining - 1, "fail_streak": fail_streak}
    if params.get("target_words"):
        next_params["target_words"] = int(params["target_words"])
    if params.get("instruction"):
        next_params["instruction"] = str(params["instruction"])
    enqueue(db, "continue_chapter", task.ref_id, next_params)
    publish(task.id, {
        "type": "status",
        "stage": "auto_continue",
        "message": f"本章结束，已自动安排下一章（剩余 {remaining - 1} 章）",
    })
    return ""
