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

    def __init__(self, db=None, novel_id=0):
        self.novel_id = novel_id
        self.cancel_check = None

    def chat_stream(self, messages, options, on_delta=None, on_stage=None):
        user = messages[1]["content"]
        no = int(re.search(r"【本章大纲】\n第(\d+)章", user).group(1))
        FakeClient.contexts[no] = user
        if FakeClient.stream_hook:
            FakeClient.stream_hook(on_delta)
        return {"text": f"正文ch{no}{FakeClient.version}"}

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
            return {"text": json.dumps({"current_state": {"plot_progress": f"PLOT[{old}|{body}]"}},
                                       ensure_ascii=False)}
        if kind == "consistency_check":
            return {"text": json.dumps({"status": "ok", "summary": "好", "issues": []})}
        return {"text": "{}"}


@pytest.fixture
def fake_ai(monkeypatch):
    FakeClient.version = "v1"
    FakeClient.contexts = {}
    FakeClient.fail = {"generate_summary": 0, "update_memory": 0}
    FakeClient.stream_hook = None
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
