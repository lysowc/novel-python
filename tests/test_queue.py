"""任务队列：小说锁、原子领取、连续续写停止、重试、取消"""
import collections
import threading
import time

from app.models import AiTask, Chapter
from app.services import ai_client, ai_service, task_service as ts
from conftest import make_novel, run_task


def test_novel_lock_semantics(S):
    a, b = ts.NovelLock(1), ts.NovelLock(1)
    assert a.acquire() and not b.acquire()
    stranger = ts.NovelLock(1)
    stranger._redis = ts.connect_redis()
    stranger.release()  # 非持有者释放无效
    assert not ts.NovelLock(1).acquire()
    a.release()
    c = ts.NovelLock(1)
    assert c.acquire()
    c.release()


def test_clear_novel_locks(S):
    lock = ts.NovelLock(7)
    assert lock.acquire()
    lock._stop.set()
    assert ts.clear_novel_locks() >= 1
    assert ts.NovelLock(7).acquire()


def _worker_loop(stop):
    while not stop.is_set():
        r = ts.connect_redis()
        item = r.brpop(ts.QUEUE_KEY, timeout=1)
        if item and not ts.execute(int(item[1])):
            time.sleep(0.05)


def test_same_novel_serial_different_novels_parallel_and_no_double_run(S, monkeypatch):
    running, peak, overall, calls = collections.Counter(), collections.Counter(), {"cur": 0, "peak": 0}, []
    guard = threading.Lock()

    class SlowAi:
        def __init__(self, db):
            self.client = type("C", (), {})()

        def _run(self, novel, task):
            with guard:
                running[novel.id] += 1
                peak[novel.id] = max(peak[novel.id], running[novel.id])
                overall["cur"] += 1
                overall["peak"] = max(overall["peak"], overall["cur"])
                calls.append(task.id)
            time.sleep(0.3)
            with guard:
                running[novel.id] -= 1
                overall["cur"] -= 1

        def generate_chapter(self, novel, task, *a):
            self._run(novel, task)

        def check_consistency(self, novel, task, *a):
            self._run(novel, task)

    monkeypatch.setattr(ai_service, "AiService", SlowAi)
    make_novel(S, 1)
    make_novel(S, 2)
    db = S()
    t1 = ts.enqueue(db, "continue_chapter", 1, {"remaining": 0})
    ts.enqueue(db, "consistency_check", 1, {})
    ts.enqueue(db, "continue_chapter", 2, {"remaining": 0})
    ts._push_task(t1.id)  # 重复入队
    db.close()

    stop = threading.Event()
    workers = [threading.Thread(target=_worker_loop, args=(stop,)) for _ in range(3)]
    [w.start() for w in workers]
    time.sleep(2.5)
    stop.set()
    [w.join() for w in workers]

    assert peak[1] == 1, "同一本小说不能并行"
    assert overall["peak"] >= 2, "不同小说应能并行"
    assert calls.count(t1.id) == 1, "重复入队的任务只执行一次"
    db = S()
    assert all(t.status == "success" for t in db.query(AiTask).all())


def test_fatal_error_stops_chain(S, fake_ai, monkeypatch):
    def boom(self, *a, **k):
        try:
            raise ai_client.AiFatalError("AI 调用失败: Invalid API key")
        except Exception as e:
            raise RuntimeError(f"章节生成失败：{e}") from e
    monkeypatch.setattr(ai_service.AiService, "generate_chapter", boom)
    make_novel(S, 1)
    task = run_task(S, "continue_chapter", 1, remaining=5)
    assert task.status == "failed"
    assert "连续续写已停止" in task.error_message and "后续 5 章" in task.error_message
    assert task.params.get("chain_stopped")
    db = S()
    assert db.query(AiTask).count() == 1


def test_three_consecutive_failures_stop_chain_and_success_resets(S, fake_ai, monkeypatch):
    def fail(self, *a, **k):
        raise RuntimeError("AI 调用失败: HTTP 503")
    make_novel(S, 1)
    original = ai_service.AiService.generate_chapter
    monkeypatch.setattr(ai_service.AiService, "generate_chapter", fail)
    run_task(S, "continue_chapter", 1, remaining=9)
    _drain(S)
    db = S()
    tasks = db.query(AiTask).order_by(AiTask.id).all()
    assert len(tasks) == 3 and all(t.status == "failed" for t in tasks)
    assert "已连续 3 章失败" in tasks[-1].error_message

    monkeypatch.setattr(ai_service.AiService, "generate_chapter", original)
    make_novel(S, 2)
    run_task(S, "continue_chapter", 2, remaining=3, fail_streak=2)
    _drain(S)
    db = S()
    assert [t.status for t in db.query(AiTask).filter_by(ref_id=2)] == ["success"] * 4


def _drain(S):
    """把链条自动排进来的任务依次执行完"""
    while True:
        db = S()
        pending = db.query(AiTask).filter_by(status="pending").order_by(AiTask.id).first()
        db.close()
        if not pending:
            return
        ts.execute(pending.id)


def test_retry_params(S):
    make_novel(S, 1)
    make_novel(S, 2)
    db = S()
    db.add(AiTask(id=10, task_type="continue_chapter", ref_id=1, params={"remaining": 4, "fail_streak": 1},
                  status="failed", error_message="x"))
    db.add(AiTask(id=11, task_type="continue_chapter", ref_id=2,
                  params={"remaining": 4, "fail_streak": 3, "chain_stopped": True}, status="failed", error_message="x"))
    db.commit()
    r = ts.connect_redis()
    r.lpush("ai:stream:10", '{"type":"done"}')
    ts.retry(db, 10)
    ts.retry(db, 11)
    a, b = db.get(AiTask, 10).params, db.get(AiTask, 11).params
    assert a["remaining"] == 0, "普通失败重试只补这一章"
    assert b["remaining"] == 4 and b["fail_streak"] == 0 and "chain_stopped" not in b, "被停止的链条重试后接着写"
    assert "done" not in str(r.lrange("ai:stream:10", 0, -1)), "重试清掉旧的进度事件"


def test_cancel_pending(S):
    make_novel(S, 1)
    db = S()
    task = ts.enqueue(db, "generate_outline", 1, {})
    task = ts.cancel(db, task.id)
    assert task.status == "cancelled"
    assert ts.execute(task.id) is True
    assert S().get(AiTask, task.id).status == "cancelled"


def test_cancel_running_stream_and_no_chain(S, fake_ai):
    make_novel(S, 1)
    db = S()
    task = ts.enqueue(db, "continue_chapter", 1, {"remaining": 3})
    tid = task.id
    db.close()

    def slow(on_delta):
        for _ in range(100):
            on_delta("字")
            time.sleep(0.03)
    fake_ai.stream_hook = slow
    th = threading.Thread(target=ts.execute, args=(tid,))
    th.start()
    time.sleep(0.5)
    ts.cancel(S(), tid)
    th.join(10)
    db = S()
    assert db.get(AiTask, tid).status == "cancelled"
    assert db.query(Chapter).count() == 0
    assert db.query(AiTask).filter_by(status="pending").count() == 0, "取消后不再续链"


def test_cancel_during_non_stream_call(S, fake_ai, monkeypatch):
    """摘要/记忆这类非流式调用等待期间也能取消（cancel_check）"""
    make_novel(S, 1)
    db = S()
    task = ts.enqueue(db, "continue_chapter", 1, {})
    tid = task.id
    db.close()
    original_chat = fake_ai.chat

    def slow_chat(self, messages, options):
        if options.get("task_type") == "generate_summary":
            for _ in range(100):  # 模拟慢请求，期间轮询取消
                time.sleep(0.05)
                if self.cancel_check:
                    self.cancel_check()
        return original_chat(self, messages, options)
    monkeypatch.setattr(fake_ai, "chat", slow_chat)
    th = threading.Thread(target=ts.execute, args=(tid,))
    th.start()
    time.sleep(0.6)
    ts.cancel(S(), tid)
    th.join(10)
    task = S().get(AiTask, tid)
    assert task.status == "cancelled"
    assert "正文已保存" in task.error_message and task.params["resume"]["chapter_no"] == 1


def test_stop_chain_after_current(S, fake_ai):
    make_novel(S, 1)
    db = S()
    task = ts.enqueue(db, "continue_chapter", 1, {"remaining": 3})
    tid = task.id
    db.close()
    fake_ai.stream_hook = lambda on_delta: ts.cancel(S(), tid, after_current=True)
    ts.execute(tid)
    task = S().get(AiTask, tid)
    assert task.status == "success" and task.params.get("chain_stopped")
    db = S()
    assert db.query(AiTask).filter_by(status="pending").count() == 0
    assert db.query(Chapter).count() == 1
