"""短剧改编：分集方案、剧本、分镜、接口"""
from app.helpers import now
from app.models import Chapter, CharacterProfile, DramaEpisode, Novel
from app.services import novel_trash
from conftest import FakeClient, add_chapter, make_novel, run_task
from test_api import client  # noqa: F401


def _eps(S, nid=1):
    db = S()
    rows = db.query(DramaEpisode).filter_by(novel_id=nid).order_by(DramaEpisode.episode_no).all()
    db.close()
    return rows


def _book(S, n=7):
    make_novel(S, 1)
    for no in range(1, n + 1):
        add_chapter(S, 1, no, f"原著第{no}章正文，沈渡又去堵温以宁")
    db = S()
    db.add(CharacterProfile(novel_id=1, name="沈渡", role="主角", appearance="寸头，黑西装", personality="自信过头",
                            created_at=now(), updated_at=now()))
    db.add(CharacterProfile(novel_id=1, name="路人甲", appearance="戴帽子", created_at=now(), updated_at=now()))
    db.commit()
    db.close()


def test_plan_appends_episodes_by_window(S, fake_ai):
    _book(S, 7)
    task = run_task(S, "drama_plan", 1)
    assert task.status == "success"
    eps = _eps(S)
    assert [e.episode_no for e in eps] == list(range(1, 8))
    assert [(e.chapter_from, e.chapter_to) for e in eps][:2] == [(1, 1), (2, 2)] and eps[-1].chapter_to == 7
    plan_calls = [c for c in FakeClient.calls if c[0] == "drama_plan"]
    assert len(plan_calls) == 2, "每 5 章切一次"
    assert "集号从第6集接着编" in plan_calls[1][2] and "第5集《集5》" in plan_calls[1][2]
    assert "寸头" in plan_calls[0][2] and "路人甲" not in plan_calls[0][2], "只带出场人物"
    # 没有新章节：报错；写了新章节：接着往后加
    assert "没有需要改编的新章节" in run_task(S, "drama_plan", 1).error_message
    add_chapter(S, 1, 8, "原著第8章")
    run_task(S, "drama_plan", 1)
    assert [(e.episode_no, e.chapter_from) for e in _eps(S)][-1] == (8, 8)


def test_script_uses_prev_next_and_only_own_chapters(S, fake_ai):
    _book(S, 3)
    run_task(S, "drama_plan", 1)
    task = run_task(S, "drama_script", 1)
    assert task.status == "success"
    eps = _eps(S)
    assert all(e.script.startswith(f"剧本ep{e.episode_no}") for e in eps)
    ctx = FakeClient.drama_inputs[2]
    assert "【上一集】\n第1集《集1》" in ctx and "上一集剧本结尾" in ctx and "台词台词" in ctx
    assert "第3集《集3》：梗概3" in ctx and "一句都不要演" in ctx
    assert "原著第2章正文" in ctx and "原著第3章正文" not in ctx and "原著第1章正文" not in ctx
    assert "寸头" in ctx
    # 都写过了：没有要写的
    assert "没有需要写剧本" in run_task(S, "drama_script", 1).error_message
    # 指定某一集：重写
    FakeClient.drama_inputs = {}
    run_task(S, "drama_script", 1, episode_ids=[eps[0].id])
    assert list(FakeClient.drama_inputs) == [1]


def test_storyboard_needs_script_and_parses_shots(S, fake_ai):
    _book(S, 2)
    run_task(S, "drama_plan", 1)
    assert "要先有剧本" in run_task(S, "drama_storyboard", 1).error_message
    run_task(S, "drama_script", 1)
    assert run_task(S, "drama_storyboard", 1).status == "success"
    from app.services import drama
    shots = drama.load_storyboard(_eps(S)[0])
    assert [s["no"] for s in shots] == [1, 2], "空镜头丢掉、重新编号"
    assert shots[0]["duration"] == 15 and shots[0]["characters"] == ["沈渡", "温以宁"]
    sb_call = [c for c in FakeClient.calls if c[0] == "drama_storyboard"][0]
    assert "寸头，黑西装" in sb_call[2] and "【本集剧本】\n剧本ep1\n沈渡：" in sb_call[2]


def test_drama_api_edit_delete_and_source_changed(S, fake_ai, client):  # noqa: F811
    _book(S, 4)
    run_task(S, "drama_plan", 1)
    run_task(S, "drama_script", 1)
    r = client.get("/api/admin/novels/1/drama").json()["data"]
    assert r["covered_until"] == 4 and r["written_until"] == 4 and r["episode_seconds"] == 90
    assert all(e["has_script"] and not e["source_changed"] for e in r["episodes"])
    # 原著第 2 章改了 → 第 2 集提示原著已改
    import datetime
    db = S()
    ch = db.query(Chapter).filter_by(novel_id=1, chapter_no=2).one()
    ch.updated_at = now() + datetime.timedelta(minutes=5)
    db.commit()
    db.close()
    eps = client.get("/api/admin/novels/1/drama").json()["data"]["episodes"]
    assert [e["source_changed"] for e in eps] == [False, True, False, False]
    # 编辑剧本
    r = client.put(f"/api/admin/drama/episodes/{eps[0]['id']}", json={"script": "改过的剧本", "title": "新名"}).json()
    assert r["data"]["script"] == "改过的剧本" and r["data"]["title"] == "新名"
    assert client.get(f"/api/admin/drama/episodes/{eps[0]['id']}").json()["data"]["script"] == "改过的剧本"
    # 删中间的集：重新编号；删最后的集：可以重新改编那几章
    client.post("/api/admin/novels/1/drama/batch-delete", json={"ids": [eps[1]["id"], eps[3]["id"]]})
    left = _eps(S)
    assert [(e.episode_no, e.chapter_from) for e in left] == [(1, 1), (2, 3)]
    run_task(S, "drama_plan", 1)
    assert [(e.episode_no, e.chapter_from) for e in _eps(S)][-1] == (3, 4)
    # 清空 + 彻底删除小说时一起删
    assert client.post("/api/admin/novels/1/drama/clear").json()["data"]["deleted"] == 3
    run_task(S, "drama_plan", 1)
    db = S()
    novel_trash.purge(db, db.get(Novel, 1))
    db.commit()
    assert db.query(DramaEpisode).count() == 0
    db.close()
