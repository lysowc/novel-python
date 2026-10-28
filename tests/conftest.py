"""测试基础设施：sqlite 临时库 + fakeredis + 假 AI 客户端，不需要 MySQL / Redis / 真实模型

运行：.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q
"""
import json
import re
import sys
from pathlib import Path

import fakeredis
import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(type_, compiler, **kw):  # sqlite 只有 INTEGER 主键才自增
    return "INTEGER"


import app.db as dbm  # noqa: E402
import app.models  # noqa: E402,F401
from app.db import Base  # noqa: E402
from app.helpers import now  # noqa: E402
from app.models import AiTask, Chapter, Novel  # noqa: E402
from app.services import ai_service, prompt_service, task_service  # noqa: E402


@pytest.fixture
def S(tmp_path, monkeypatch):
    """每个测试一个独立 sqlite 库 + 独立 fakeredis；返回 SessionLocal"""
    engine = create_engine(f"sqlite:///{tmp_path}/test.db",
                           connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    dbm.SessionLocal.configure(bind=engine)
    server = fakeredis.FakeServer()
    monkeypatch.setattr(task_service, "connect_redis",
                        lambda: fakeredis.FakeRedis(server=server, decode_responses=True))
    monkeypatch.setattr(task_service, "connect_redis_async",
                        lambda: FakeAsyncRedis(server=server, decode_responses=True))
    monkeypatch.setattr(prompt_service, "render", lambda db, key, variables=None: "sys")
    # 假 AI 的正文很短，默认关掉质量检查（质量检查有专门的测试）
    from app.models import SystemConfig
    db = dbm.SessionLocal()
    db.add(SystemConfig(key="quality_check_enabled", value="0"))
    # 自动审校 / 重复检测有专门的测试；默认关掉，避免假 AI 的固定摘要触发提示、多出审校任务
    db.add(SystemConfig(key="consistency_auto_interval", value="0"))
    db.add(SystemConfig(key="repetition_check_enabled", value="0"))
    db.commit()
    db.close()
    yield dbm.SessionLocal
    dbm.SessionLocal.configure(bind=dbm.engine)
    engine.dispose()


def plot(text: str) -> str:
    m = re.search(r"PLOT\[(.*?)\]", text or "")
    return m.group(1) if m else ""


class FakeClient:
    """假 AI：正文 = "正文ch{N}{version}"；记忆把每章正文追加到 PLOT[...] 里，便于断言"用了哪个版本的哪一章" """
    version = "v1"
    contexts: dict = {}
    fail = {"generate_summary": 0, "update_memory": 0}
    stream_hook = None
    stream_texts: list = []  # 非空时按顺序作为正文返回（测试质量检查）
    finish_reasons: list = []
    patches: dict = {}        # 章号 → 额外的记忆补丁字段（dict 或 正文 → dict 的函数）
    memory_inputs: dict = {}  # 章号 → 更新记忆时 AI 收到的输入
    arc_calls: list = []

    def __init__(self, db=None, novel_id=0):
        self.novel_id = novel_id
        self.cancel_check = None

    def chat_stream(self, messages, options, on_delta=None, on_stage=None):
        user = messages[1]["content"]
        no = int(re.search(r"【本章大纲】\n第(\d+)章", user).group(1))
        FakeClient.contexts[no] = user
        if FakeClient.stream_hook:
            FakeClient.stream_hook(on_delta)
        text = FakeClient.stream_texts.pop(0) if FakeClient.stream_texts else f"正文ch{no}{FakeClient.version}"
        if on_delta:
            on_delta(text)
        reason = FakeClient.finish_reasons.pop(0) if FakeClient.finish_reasons else "stop"
        return {"text": text, "finish_reason": reason}

    def chat(self, messages, options):
        kind = options.get("task_type")
        if FakeClient.fail.get(kind, 0) > 0:
            FakeClient.fail[kind] -= 1
            raise RuntimeError("AI 请求失败: timeout")
        user = messages[-1]["content"]
        if kind == "generate_summary":
            return {"text": "摘要"}
        if kind == "update_memory":
            old = plot(user.split("【最新一章】")[0])
            body = re.search(r"本章正文：\n(.*)", user, re.S).group(1).strip()
            no = int(re.search(r"【最新一章】\n第(\d+)章", user).group(1))
            patch = {"current_state": {"plot_progress": f"PLOT[{old}|{body}]"},
                     "timeline_add": [f"事件{no}"]}
            extra = FakeClient.patches.get(no)
            if callable(extra):
                extra = extra(body)
            patch.update(extra or {})
            FakeClient.memory_inputs[no] = user
            return {"text": json.dumps(patch, ensure_ascii=False)}
        if kind == "arc_summary":
            nos = re.findall(r"^第(\d+)章", user.split("】")[-1], re.M)
            FakeClient.arc_calls.append((int(nos[0]), int(nos[-1])))
            return {"text": f"ARC[{nos[0]}-{nos[-1]}]"}
        if kind == "extend_outline":
            return {"text": json.dumps({"volumes": [{"title": "第二卷 新篇", "chapters": [
                {"no": 99, "title": f"新章{i}", "summary": f"新剧情{i}"} for i in range(1, 4)]}]}, ensure_ascii=False)}
        if kind == "consistency_check":
            return {"text": json.dumps({"status": "ok", "summary": "好", "issues": []})}
        return {"text": "{}"}


@pytest.fixture
def fake_ai(monkeypatch):
    FakeClient.version = "v1"
    FakeClient.contexts = {}
    FakeClient.fail = {"generate_summary": 0, "update_memory": 0}
    FakeClient.stream_hook = None
    FakeClient.stream_texts = []
    FakeClient.finish_reasons = []
    FakeClient.patches = {}
    FakeClient.memory_inputs = {}
    FakeClient.arc_calls = []
    monkeypatch.setattr(ai_service, "AiClient", FakeClient)
    return FakeClient


def make_novel(S, nid: int, **fields) -> None:
    db = S()
    novel = Novel(id=nid, title=fields.pop("title", f"小说{nid}"), cover="", tags="", status="draft",
                  is_public=0, word_count=0, chapter_count=0, created_at=now(), updated_at=now(), **fields)
    db.add(novel)
    db.commit()
    db.close()


def add_chapter(S, nid: int, no: int, content: str, status: str = "published", summary: str = "s") -> None:
    db = S()
    db.add(Chapter(novel_id=nid, chapter_no=no, title=f"第{no}章", content=content, summary=summary,
                   status=status, word_count=len(content), created_at=now(), updated_at=now()))
    db.commit()
    db.close()


def run_task(S, task_type: str, novel_id: int, **params) -> AiTask:
    """创建一个 pending 任务并在当前线程执行，返回执行后的任务"""
    db = S()
    task = AiTask(task_type=task_type, ref_id=novel_id, ref_type="novel", params=params,
                  status="pending", error_message="", created_at=now(), updated_at=now())
    db.add(task)
    db.commit()
    tid = task.id
    db.close()
    task_service.execute(tid)
    db = S()
    task = db.get(AiTask, tid)
    db.close()
    return task


def current_plot(S, nid: int) -> str:
    from app.models import NovelMemory
    db = S()
    row = db.query(NovelMemory).filter_by(novel_id=nid).first()
    db.close()
    return plot(row.content) if row else ""


def snapshot_plots(S, nid: int) -> dict:
    from app.models import NovelMemorySnapshot
    db = S()
    rows = {s.chapter_no: plot(s.content) for s in db.query(NovelMemorySnapshot).filter_by(novel_id=nid)}
    db.close()
    return rows
