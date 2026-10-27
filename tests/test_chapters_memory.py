"""章节生成、记忆快照、断点补做、记忆重建、记忆合并"""
import json

from app.models import AiTask, Chapter, NovelMemory, SystemConfig
from app.services import memory as mem
from app.services import memory_store
from conftest import add_chapter, current_plot, make_novel, run_task, snapshot_plots


def test_continue_never_overwrites_and_generate_refuses_existing(S, fake_ai):
    make_novel(S, 1)
    for _ in range(2):
        run_task(S, "continue_chapter", 1)
    run_task(S, "continue_chapter", 1, chapter_no=2)  # 续写忽略章号
    db = S()
    assert [c.chapter_no for c in db.query(Chapter).order_by(Chapter.chapter_no)] == [1, 2, 3]
    task = run_task(S, "generate_chapter", 1, chapter_no=2)
    assert task.status == "failed" and "已存在" in task.error_message


def test_regenerate_uses_only_earlier_context_and_snapshot(S, fake_ai):
    make_novel(S, 1)
    for _ in range(3):
        run_task(S, "continue_chapter", 1)
    assert current_plot(S, 1) == "|正文ch1v1|正文ch2v1|正文ch3v1"

    fake_ai.version = "v2"
    run_task(S, "regenerate_chapter", 1, chapter_no=2)
    ctx = fake_ai.contexts[2]
    assert "正文ch3" not in ctx and "正文ch2v1" not in ctx, "不能看到后续章节和本章旧版本"
    assert "PLOT[|正文ch1v1]" in ctx, "记忆用第 1 章结束时的快照"
    assert current_plot(S, 1) == "|正文ch1v1|正文ch2v1|正文ch3v1", "中间章节只更新快照"
    assert snapshot_plots(S, 1)[2] == "|正文ch1v1|正文ch2v2"

    run_task(S, "regenerate_chapter", 1, chapter_no=3)
    assert current_plot(S, 1) == "|正文ch1v1|正文ch2v2|正文ch3v2", "重写最后一章不叠加旧版本"


def test_regenerate_keeps_manual_title(S, fake_ai):
    make_novel(S, 1)
    run_task(S, "continue_chapter", 1)
    db = S()
    c = db.query(Chapter).one()
    c.title = "我改的标题"
    db.commit()
    run_task(S, "regenerate_chapter", 1, chapter_no=1)
    assert S().query(Chapter).one().title == "我改的标题"


def test_legacy_novel_without_snapshots(S, fake_ai):
    make_novel(S, 2)
    add_chapter(S, 2, 1, "旧1")
    add_chapter(S, 2, 2, "旧2")
    db = S()
    db.add(NovelMemory(novel_id=2, content=json.dumps({"current_state": {"plot_progress": "PLOT[|旧1|旧2]"}}, ensure_ascii=False)))
    db.commit()
    run_task(S, "continue_chapter", 2)
    snaps = snapshot_plots(S, 2)
    assert snaps[2] == "|旧1|旧2", "写新章时补存上一章快照作为基线"
    run_task(S, "regenerate_chapter", 2, chapter_no=3)
    assert current_plot(S, 2) == "|旧1|旧2|正文ch3v1"
    run_task(S, "regenerate_chapter", 2, chapter_no=2)
    assert memory_store.STALE_NOTE in fake_ai.contexts[2], "无快照时带提示"


def test_rebuild_memory_from_chapter(S, fake_ai):
    make_novel(S, 1)
    for _ in range(4):
        run_task(S, "continue_chapter", 1)
    fake_ai.version = "v2"
    run_task(S, "regenerate_chapter", 1, chapter_no=2)
    assert current_plot(S, 1) == "|正文ch1v1|正文ch2v1|正文ch3v1|正文ch4v1"
    task = run_task(S, "rebuild_memory", 1, chapter_no=2)
    assert task.status == "success", task.error_message
    assert current_plot(S, 1) == "|正文ch1v1|正文ch2v2|正文ch3v1|正文ch4v1"
    assert snapshot_plots(S, 1)[3] == "|正文ch1v1|正文ch2v2|正文ch3v1"


def test_rebuild_requires_snapshot_or_start_from_one(S, fake_ai):
    make_novel(S, 2)
    for no in (1, 2, 3):
        add_chapter(S, 2, no, f"旧{no}")
    task = run_task(S, "rebuild_memory", 2, chapter_no=2)
    assert task.status == "failed" and "从第 1 章开始" in task.error_message
    task = run_task(S, "rebuild_memory", 2, chapter_no=1)
    assert task.status == "success"
    assert current_plot(S, 2) == "|旧1|旧2|旧3"


def test_summary_failure_auto_retry_then_resume(S, fake_ai):
    make_novel(S, 1)
    run_task(S, "continue_chapter", 1)
    fake_ai.fail["generate_summary"] = 1  # 失败一次 → 自动重试成功
    assert run_task(S, "continue_chapter", 1).status == "success"

    fake_ai.fail["update_memory"] = 2  # 两次都失败 → 任务失败，记下断点
    task = run_task(S, "continue_chapter", 1)
    assert task.status == "failed" and task.params["resume"]["chapter_no"] == 3

    from app.services import task_service as ts
    db = S()
    ts.retry(db, task.id)
    db.close()
    fake_ai.contexts.clear()
    ts.execute(task.id)
    assert S().get(AiTask, task.id).status == "success"
    assert 3 not in fake_ai.contexts, "补做不重新生成正文"
    assert S().query(Chapter).count() == 3
    assert current_plot(S, 1) == "|正文ch1v1|正文ch2v1|正文ch3v1"


def test_resume_for_deleted_chapter_clears_breakpoint(S, fake_ai):
    make_novel(S, 1)
    task = run_task(S, "continue_chapter", 1, resume={"chapter_no": 9})
    assert task.status == "failed" and "不存在" in task.error_message and "resume" not in task.params


def test_drafts_when_auto_publish_off(S, fake_ai):
    make_novel(S, 1)
    db = S()
    db.add(SystemConfig(key="chapter_auto_publish", value="0"))
    db.commit()
    run_task(S, "continue_chapter", 1)
    run_task(S, "continue_chapter", 1)
    assert S().query(Chapter).filter_by(chapter_no=1).one().status == "draft"
    assert "正文ch1v1" in fake_ai.contexts[2], "草稿进入后续上下文"


def test_merge_memory_restores_dropped_items():
    old = json.dumps(mem.normalize_memory({
        "schema": "v2",
        "characters": [{"name": "林凡"}, {"name": "老李", "status": "受伤"}],
        "foreshadowing": [{"description": "古戒中封印着残魂", "status": "open"},
                          {"description": "血魔宗卧底", "status": "open"}],
        "important_items": [{"name": "青铜戒"}], "world_facts": ["青云宗禁止私斗"],
        "current_state": {"location": "青云宗"},
    }), ensure_ascii=False)
    new = mem.normalize_memory({
        "schema": "v2", "characters": [{"name": "林凡"}],
        "foreshadowing": [{"description": "古戒里封印着一缕残魂", "status": "open"}],
        "current_state": {"time": "夜"},
    })
    merged, restored = mem.merge_memory(old, new)
    assert sorted(f["description"] for f in merged["foreshadowing"]) == ["古戒里封印着一缕残魂", "血魔宗卧底"]
    assert merged["important_items"][0]["name"] == "青铜戒"
    assert merged["world_facts"] == ["青云宗禁止私斗"]
    assert [c["name"] for c in merged["character_archive"]] == ["老李"]
    assert merged["current_state"]["location"] == "青云宗"
    assert "其他已登场人物" in mem.render_structured(merged)
    assert len(restored) == 3
