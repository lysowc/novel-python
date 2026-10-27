"""HTTP 接口：登录限流、历史版本、草稿发布、前台计数、导出、统计、删除保护、任务流、点子聊天"""
import io
import json
import zipfile
from xml.dom import minidom

import bcrypt
import pytest
from fastapi.testclient import TestClient

from app.helpers import now
from app.models import Admin, AiModel, AiProvider, AiTask, Chapter, Idea, IdeaChat, Novel
from app.services import ai_client, task_service as ts
from conftest import add_chapter, make_novel, run_task


@pytest.fixture
def client(S):
    from app.main import app
    db = S()
    db.add(Admin(username="admin", password=bcrypt.hashpw(b"pw123456", bcrypt.gensalt(4)).decode(),
                 created_at=now(), updated_at=now()))
    db.commit()
    db.close()
    c = TestClient(app)
    assert c.post("/api/admin/login", json={"username": "admin", "password": "pw123456"}).json()["code"] == 0
    return c


def test_login_rate_limit(S, client):
    anon = TestClient(client.app)
    for _ in range(5):
        assert anon.post("/api/admin/login", json={"username": "admin", "password": "wrong"}).json()["msg"] == "账号或密码错误"
    r = anon.post("/api/admin/login", json={"username": "admin", "password": "pw123456"}).json()
    assert r["code"] != 0 and "次数过多" in r["msg"], "超过次数后正确密码也要等"


def test_dashboard_security_warnings(S, client):
    data = client.get("/api/admin/dashboard").json()["data"]
    assert any("SESSION_SECRET" in w for w in data["security_warnings"])


def test_versions_edit_restore_and_deleted(S, client, fake_ai):
    make_novel(S, 1)
    for _ in range(2):
        run_task(S, "continue_chapter", 1)
    fake_ai.version = "v2"
    run_task(S, "regenerate_chapter", 1, chapter_no=2)
    cid = S().query(Chapter).filter_by(chapter_no=2).one().id
    vs = client.get(f"/api/admin/chapters/{cid}/versions").json()["data"]
    assert [v["source"] for v in vs] == ["regenerate"]
    r1 = client.put(f"/api/admin/chapters/{cid}", json={"content": "手改"}).json()
    assert r1["code"] == 0, r1
    client.put(f"/api/admin/chapters/{cid}", json={"content": "手改"})  # 未变化不新增
    vs = client.get(f"/api/admin/chapters/{cid}/versions").json()["data"]
    assert len(vs) == 2
    r = client.post(f"/api/admin/chapter-versions/{vs[-1]['id']}/restore").json()
    assert r["code"] == 0 and r["data"]["content"] == "正文ch2v1"

    c1 = S().query(Chapter).filter_by(chapter_no=1).one().id
    client.delete(f"/api/admin/chapters/{c1}")
    deleted = client.get("/api/admin/novels/1/deleted-chapters").json()["data"]
    assert [d["chapter_no"] for d in deleted] == [1]
    assert client.post(f"/api/admin/chapter-versions/{deleted[0]['id']}/restore").json()["code"] == 0
    assert S().query(Chapter).filter_by(chapter_no=1).one().content == "正文ch1v1"

    db = S()
    db.add(AiTask(task_type="generate_outline", ref_id=1, params={}, status="running", error_message=""))
    db.commit()
    assert client.post(f"/api/admin/chapter-versions/{vs[0]['id']}/restore").json()["code"] != 0, "有任务时不允许恢复"


def test_publish_drafts_and_front_counts(S, client):
    make_novel(S, 1, description="d")
    db = S()
    n = db.get(Novel, 1)
    n.is_public, n.status = 1, "published"
    db.commit()
    add_chapter(S, 1, 1, "一二三", status="draft")
    add_chapter(S, 1, 2, "四五六七", status="published")
    from app.routers.admin.novels import _recount_novel
    db = S()
    _recount_novel(db, db.get(Novel, 1))
    front = client.get("/api/novels/1").json()["data"]
    assert front["chapter_count"] == 1 and front["word_count"] == 4, "前台只算已发布章节"
    assert front["first_no"] == 2, "开始阅读不能指向草稿"
    assert client.get("/api/novels").json()["data"]["list"][0]["chapter_count"] == 1
    assert client.post("/api/admin/novels/1/chapters/publish", json={}).json()["data"]["count"] == 1
    assert client.get("/api/novels/1").json()["data"]["chapter_count"] == 2


def test_export_epub_txt(S, client):
    make_novel(S, 1, title="测试<书>", description="简介&说明")
    add_chapter(S, 1, 1, "第一段\n第二段")
    add_chapter(S, 1, 2, "草稿内容", status="draft")
    r = client.get("/api/admin/novels/1/export?format=epub")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert names[0] == "mimetype" and z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
    for f in names:
        if f.endswith((".xhtml", ".opf", ".ncx", ".xml")):
            minidom.parseString(z.read(f))
    assert "OEBPS/c0002.xhtml" not in names, "默认不导出草稿"
    txt = client.get("/api/admin/novels/1/export?format=txt&include_drafts=1").content.decode("utf-8-sig")
    assert "　　第一段" in txt and "草稿内容" in txt
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    assert client.get("/api/admin/novels/999/export").json()["code"] != 0


def test_usage_stats(S, client):
    make_novel(S, 5)
    db = S()
    p = AiProvider(name="p", base_url="x", api_key="", status=1, is_default=1)
    db.add(p)
    db.commit()
    m = AiModel(provider_id=p.id, name="m", display_name="m", price_input=2, price_output=8, status=1, is_default=1)
    db.add(m)
    db.commit()
    ai_client.log_call(db, "generate_chapter", p, m, {"prompt_tokens": 1_000_000, "completion_tokens": 500_000},
                       10, "success", "", "", 5)
    ai_client.log_call(db, "idea_chat", p, m, ai_client.estimate_usage([{"content": "你好"}], "回复"),
                       10, "failed", "x", "", 0)
    data = client.get("/api/admin/stats/usage?days=7").json()["data"]
    assert abs(data["totals"]["cost"] - 6.0) < 1e-6
    assert data["totals"]["estimated"] == 1 and data["totals"]["failed"] == 1
    assert len(data["by_day"]) == 7 and data["by_day"][-1]["calls"] == 2
    assert {r["novel_id"] for r in data["by_novel"]} == {0, 5}


def test_delete_novel_guard_and_cleanup(S, client):
    make_novel(S, 1)
    add_chapter(S, 1, 1, "x")
    db = S()
    db.add(Idea(id=1, novel_id=1, title="i", content="", status="used"))
    db.add(AiTask(task_type="generate_outline", ref_id=1, params={}, status="pending", error_message=""))
    db.commit()
    assert client.delete("/api/admin/novels/1").json()["code"] != 0
    db.query(AiTask).update({"status": "success"})
    db.commit()
    lock = ts.NovelLock(1)
    lock.acquire()
    assert client.delete("/api/admin/novels/1").json()["code"] != 0, "持锁期间拒绝"
    lock.release()
    assert client.delete("/api/admin/novels/1").json()["code"] == 0
    db = S()
    assert db.get(Novel, 1) is None and db.query(AiTask).count() == 0 and db.get(Idea, 1).novel_id is None


def test_task_stream_incremental_and_replay(S, client):
    make_novel(S, 1)
    db = S()
    db.add(AiTask(id=1, task_type="continue_chapter", ref_id=1, params={}, status="running", error_message=""))
    db.commit()
    ts.publish(1, {"type": "status", "message": "开始"})
    for i in range(300):
        ts.publish(1, {"type": "chunk", "content": str(i)})
    ts.finish(1, "success")
    for _ in range(2):  # 第二次 = 断线重连回放
        with client.stream("GET", "/api/admin/ai/tasks/1/stream") as r:
            events = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
        assert [e["content"] for e in events if e["type"] == "chunk"] == [str(i) for i in range(300)]
        assert events[-1]["type"] == "done"


def test_cancel_and_retry_endpoints(S, client):
    make_novel(S, 1)
    db = S()
    task = ts.enqueue(db, "generate_outline", 1, {})
    r = client.post(f"/api/admin/ai/tasks/{task.id}/cancel", json={}).json()
    assert r["data"]["status"] == "cancelled"
    r = client.post(f"/api/admin/ai/tasks/{task.id}/retry").json()
    assert r["code"] == 0 and r["data"]["status"] == "pending"


def test_idea_chat_stream(S, client, monkeypatch):
    class FakeChat:
        def __init__(self, db, novel_id=0):
            pass

        def chat_stream(self, messages, options, on_delta=None, on_stage=None):
            for ch in "你好呀":
                on_delta(ch)
            return {"text": "你好呀"}
    monkeypatch.setattr(ai_client, "AiClient", FakeChat)
    db = S()
    db.add(Idea(id=1, title="点子", content="", status="unused"))
    db.commit()
    with client.stream("POST", "/api/admin/ideas/1/chat", json={"message": "hi"}) as r:
        events = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    assert "".join(e["content"] for e in events if e["type"] == "delta") == "你好呀"
    assert events[-1]["type"] == "done"
    assert [c.role for c in S().query(IdeaChat).order_by(IdeaChat.id)] == ["user", "assistant"]
