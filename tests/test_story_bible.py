"""长篇资料库：记忆增量补丁、人物档案/境界历史、名词表、分段总结、重复检测"""
import json

from app.models import AiTask, ArcSummary, CharacterProfile, NovelMemory, StoryTerm, SystemConfig
from app.services import memory as mem
from app.services import story_bible
from conftest import current_plot, make_novel, run_task
from test_api import client  # noqa: F401  复用登录好的 TestClient


def _config(S, **kv):
    db = S()
    for k, v in kv.items():
        row = db.query(SystemConfig).filter_by(key=k).first()
        if row:
            row.value = str(v)
        else:
            db.add(SystemConfig(key=k, value=str(v)))
    db.commit()
    db.close()


def _memory(S, nid=1) -> dict:
    db = S()
    row = db.query(NovelMemory).filter_by(novel_id=nid).first()
    db.close()
    return json.loads(row.content)


# ---------- 补丁合并（纯函数） ----------

def test_apply_patch_keeps_untouched_and_resolves_fuzzy():
    old = json.dumps(mem.normalize_memory({
        "current_state": {"location": "青云宗", "time": "入门第一年", "plot_progress": "拜师"},
        "characters": [{"name": "林舟", "status": "炼气三层"}, {"name": "王二", "status": "杂役"}],
        "foreshadowing": [{"description": "后山禁地传来的钟声", "planted_chapter": 2, "status": "open"},
                          {"description": "师父腰间的断剑", "planted_chapter": 3, "status": "open"}],
        "world_facts": ["宗门每十年大比"],
        "important_items": [{"name": "青铜灯", "status": "林舟持有"}],
        "unresolved_events": ["查明灭门真凶"],
    }), ensure_ascii=False)
    patch = {
        "current_state": {"plot_progress": "下山历练"},
        "story_time": "入门第二年春",
        "characters_upsert": [{"name": "林舟", "status": "炼气五层"}, {"name": "苏晚", "status": "初遇"}],
        "characters_left": ["王二"],
        "foreshadowing_resolve": ["后山禁地的钟声"],  # 换了个说法
        "foreshadowing_add": ["苏晚袖中的血玉"],
        "unresolved_resolve": ["灭门真凶查明"],
        "items_upsert": [{"name": "血玉", "status": "苏晚持有"}],
        "timeline_add": ["林舟下山"],
    }
    out, notes = mem.apply_patch(old, patch, 5)
    assert out["current_state"] == {"location": "青云宗", "time": "入门第二年春", "plot_progress": "下山历练"}
    names = {c["name"]: c for c in out["characters"]}
    assert names["林舟"]["status"] == "炼气五层" and "苏晚" in names and "王二" not in names
    assert [a["name"] for a in out["character_archive"]] == ["王二"]
    fores = {f["description"]: f for f in out["foreshadowing"]}
    assert fores["后山禁地传来的钟声"]["status"] == "resolved" and fores["后山禁地传来的钟声"]["resolved_chapter"] == 5
    assert fores["师父腰间的断剑"]["status"] == "open", "没提到的伏笔原样保留"
    assert fores["苏晚袖中的血玉"]["planted_chapter"] == 5
    assert out["world_facts"] == ["宗门每十年大比"]
    assert {i["name"] for i in out["important_items"]} == {"青铜灯", "血玉"}
    assert out["unresolved_events"] == []
    assert out["timeline"] == [{"chapter": 5, "event": "林舟下山"}]
    assert any("新人物：苏晚" in n for n in notes)


def test_apply_patch_caps_active_characters_by_recency():
    old = json.dumps(mem.normalize_memory({"characters": [
        {"name": f"人{i}", "status": "", "last_chapter": i} for i in range(mem.ACTIVE_CHAR_LIMIT)]}), ensure_ascii=False)
    out, _ = mem.apply_patch(old, {"characters_upsert": [{"name": "新人", "status": "登场"}]}, 100)
    active = {c["name"] for c in out["characters"]}
    assert len(active) == mem.ACTIVE_CHAR_LIMIT and "新人" in active and "人0" not in active
    assert "人0" in {a["name"] for a in out["character_archive"]}, "挤出去的进名册，不会丢"


def test_is_patch_distinguishes_full_memory():
    assert mem.is_patch({"characters_upsert": []})
    assert not mem.is_patch({"schema": "v2", "characters": []})
    assert not mem.is_patch({"current_state": {"plot_progress": "x"}})


# ---------- 生成链路 ----------

def test_profiles_terms_realm_history_into_context(S, fake_ai):
    make_novel(S, 1)
    fake_ai.patches = {
        1: {"profiles": [{"name": "林舟", "role": "主角", "personality": "隐忍", "realm": "炼气一层"},
                         {"name": "赵虎", "role": "反派", "appearance": "独眼", "aliases": "虎爷"}],
            "terms": [{"term": "青云宗", "type": "势力", "description": "主角所在宗门"}]},
        3: {"profiles": [{"name": "林舟", "realm": "筑基初期"}],
            "terms": [{"term": "青云宗", "description": "x"}]},  # 已有名词不被短描述覆盖
    }
    for _ in range(4):
        run_task(S, "continue_chapter", 1)
    db = S()
    lin = db.query(CharacterProfile).filter_by(novel_id=1, name="林舟").one()
    assert lin.realm == "筑基初期"
    assert lin.realm_history == [{"chapter": 1, "realm": "炼气一层"}, {"chapter": 3, "realm": "筑基初期"}]
    assert db.query(StoryTerm).filter_by(term="青云宗").one().description == "主角所在宗门"
    ctx = fake_ai.contexts[4]
    assert "【本章相关人物档案】" in ctx and "筑基初期" in ctx and "独眼" in ctx
    assert "【名词表】" in ctx and "青云宗【势力】：主角所在宗门" in ctx
    assert "林舟" in fake_ai.memory_inputs[4].split("【名词表】")[0], "更新记忆时给 AI 看档案索引"
    assert story_bible.realm_at(lin, 3) == "炼气一层", "重写第 3 章时看到的是第 3 章之前的境界"


def test_rewrite_rolls_back_ai_profiles_but_keeps_locked(S, fake_ai):
    make_novel(S, 1)
    fake_ai.patches = {2: lambda body: {"profiles": [{"name": "路人甲"}], "terms": ["黑风寨"]}
                       if body.endswith("v1") else {}}
    for _ in range(3):
        run_task(S, "continue_chapter", 1)
    db = S()
    db.add(CharacterProfile(novel_id=1, name="作者角色", aliases="", role="配角", realm="", status="",
                            first_chapter=2, last_chapter=2, locked=1))
    db.commit()
    db.close()
    fake_ai.version = "v2"
    run_task(S, "regenerate_chapter", 1, chapter_no=2)
    db = S()
    assert {p.name for p in db.query(CharacterProfile)} == {"作者角色"}, "旧版第 2 章建的档案撤回，作者锁定的保留"
    assert db.query(StoryTerm).count() == 0
    assert current_plot(S, 1).endswith("正文ch3v1")


def test_rebuild_from_one_reextracts_profiles(S, fake_ai):
    make_novel(S, 1)
    fake_ai.patches = {1: {"profiles": [{"name": "林舟", "realm": "炼气"}]}}
    run_task(S, "continue_chapter", 1)
    run_task(S, "continue_chapter", 1)
    fake_ai.patches = {2: {"profiles": [{"name": "林舟", "realm": "筑基"}]}}
    assert run_task(S, "rebuild_memory", 1, chapter_no=1).status == "success"
    lin = S().query(CharacterProfile).filter_by(name="林舟").one()
    assert lin.realm == "筑基" and lin.realm_history == [{"chapter": 2, "realm": "筑基"}]


def test_arc_summaries_generated_invalidated_and_in_context(S, fake_ai):
    _config(S, arc_summary_size=3)
    make_novel(S, 1)
    for _ in range(7):
        run_task(S, "continue_chapter", 1)
    assert fake_ai.arc_calls == [(1, 3), (4, 6)]
    ctx = fake_ai.contexts[7]
    assert "【前情总览（分段总结）】" in ctx and "第1-3章：ARC[1-3]" in ctx and "第4-6章：ARC[4-6]" in ctx
    assert "ARC[4-6]" not in fake_ai.contexts[6], "写第 6 章时这一段还没写完"

    fake_ai.version = "v2"
    run_task(S, "regenerate_chapter", 1, chapter_no=2)
    assert fake_ai.arc_calls[-1] == (1, 3), "重写段内章节后该段重新总结"

    t = run_task(S, "arc_summary", 1, chapter_no=5)
    assert t.status == "success" and fake_ai.arc_calls[-1] == (4, 6)
    t = run_task(S, "arc_summary", 1, chapter_no=8)
    assert t.status == "failed" and "还没写完" in t.error_message
    assert S().query(ArcSummary).count() == 2


def test_arc_failure_does_not_fail_chapter(S, fake_ai):
    _config(S, arc_summary_size=2)
    make_novel(S, 1)
    fake_ai.fail["arc_summary"] = 2  # 自动重试一次也失败
    run_task(S, "continue_chapter", 1)
    t = run_task(S, "continue_chapter", 1)
    assert t.status == "success"
    assert S().query(ArcSummary).count() == 0
    run_task(S, "continue_chapter", 1)  # 下一章时补齐
    assert S().query(ArcSummary).count() == 1


def test_repetition_warning(S, fake_ai):
    _config(S, repetition_check_enabled=1)
    make_novel(S, 1)
    for _ in range(3):
        task = run_task(S, "continue_chapter", 1)
    assert "第1章" in (task.params or {}).get("repetition_warning", ""), "假 AI 的摘要都一样 → 与第 1 章重复"
    assert "第2章" not in task.params["repetition_warning"], "紧挨着的上一章不算"


def test_auto_consistency_default_on(S, fake_ai):
    db = S()
    db.query(SystemConfig).filter_by(key="consistency_auto_interval").delete()
    db.commit()
    make_novel(S, 1)
    for _ in range(10):
        run_task(S, "continue_chapter", 1)
    assert S().query(AiTask).filter_by(task_type="consistency_check").count() == 1


def test_bible_api(S, client, fake_ai):  # noqa: F811
    make_novel(S, 1)
    fake_ai.patches = {1: {"profiles": [{"name": "林舟", "realm": "炼气"}], "terms": ["青云宗"]}}
    run_task(S, "continue_chapter", 1)
    data = client.get("/api/admin/novels/1/bible").json()["data"]
    assert [p["name"] for p in data["profiles"]] == ["林舟"] and data["terms"][0]["term"] == "青云宗"
    pid = data["profiles"][0]["id"]

    r = client.post("/api/admin/novels/1/profiles", json={"name": "林舟"}).json()
    assert r["code"] != 0 and "同名" in r["msg"]
    r = client.post("/api/admin/novels/1/profiles", json={"name": "苏晚", "personality": "清冷"}).json()
    assert r["code"] == 0 and r["data"]["locked"] == 1, "手动建的档案默认锁定"

    r = client.put(f"/api/admin/profiles/{pid}", json={"realm": "筑基", "locked": 1}).json()["data"]
    assert r["realm"] == "筑基" and r["locked"] == 1 and r["realm_history"][-1]["realm"] == "筑基"
    # 锁定后 AI 不改
    fake_ai.patches = {2: {"profiles": [{"name": "林舟", "realm": "金丹", "personality": "暴躁"}]}}
    run_task(S, "continue_chapter", 1)
    lin = next(p for p in client.get("/api/admin/novels/1/bible").json()["data"]["profiles"] if p["name"] == "林舟")
    assert lin["realm"] == "筑基" and lin["personality"] == "" and lin["last_chapter"] == 2

    tid = data["terms"][0]["id"]
    assert client.put(f"/api/admin/terms/{tid}", json={"description": "大宗门"}).json()["data"]["description"] == "大宗门"
    assert client.post("/api/admin/novels/1/terms", json={"term": "青云宗"}).json()["code"] != 0
    assert client.delete(f"/api/admin/terms/{tid}").json()["code"] == 0
    assert client.delete(f"/api/admin/profiles/{pid}").json()["code"] == 0
    assert [p["name"] for p in client.get("/api/admin/novels/1/bible").json()["data"]["profiles"]] == ["苏晚"]


def test_mark_appearances_tracks_regulars_by_text(S):
    """主角每章都出场但 AI 补丁没提到他时，最近出场章也要跟上，否则会被挤出本章相关人物"""
    make_novel(S, 1)
    db = S()
    db.add(CharacterProfile(novel_id=1, name="沈砚", aliases="言三郎,砚", role="主角", realm="", status="",
                            realm_history=[], first_chapter=1, last_chapter=2, locked=0))
    db.add(CharacterProfile(novel_id=1, name="顾青萝", aliases="", role="", realm="", status="",
                            realm_history=[], first_chapter=1, last_chapter=2, locked=1))
    db.add(CharacterProfile(novel_id=1, name="后来人", aliases="", role="", realm="", status="",
                            realm_history=[], first_chapter=9, last_chapter=9, locked=0))
    db.commit()
    n = story_bible.mark_appearances(db, 1, 8, "言三郎推门而入，顾青萝抬头。砚台上墨迹未干。后来人未至。")
    db.commit()
    rows = {p.name: p.last_chapter for p in story_bible.profiles(db, 1)}
    db.close()
    assert n == 2
    assert rows["沈砚"] == 8, "按别名认出来"
    assert rows["顾青萝"] == 8, "锁定的档案也记录出场"
    assert rows["后来人"] == 9, "还没登场的人不受影响"


# ---------- 长篇不忘：伏笔/设定不删，写章节时按相关性挑选 ----------

def test_open_foreshadowing_never_dropped_and_major_kept():
    old = json.dumps(mem.normalize_memory({"foreshadowing": [
        {"description": f"第{i}章的小悬念编号{i}", "planted_chapter": i, "status": "open"} for i in range(1, 121)]
        + [{"description": "沈家地窖里的樟木箱子装着什么", "planted_chapter": 1, "status": "open", "major": 1}],
        "world_facts": [f"设定{i}：某地某事{i}" for i in range(150)]}), ensure_ascii=False)
    out, notes = mem.apply_patch(old, {
        "foreshadowing_add": [{"description": "青衫女子腰间的鹞鹰令牌", "major": True}, "柜房木匣里的东西"],
        "world_facts_add": ["雁回镇北门夜里不关"],
    }, 130)
    opened = [f for f in out["foreshadowing"] if f["status"] == "open"]
    assert len(opened) == 123, "未回收的伏笔一条都不能删"
    assert any(f["description"].startswith("第1章的小悬念") for f in opened)
    assert {f["description"] for f in opened if f.get("major")} == {"沈家地窖里的樟木箱子装着什么", "青衫女子腰间的鹞鹰令牌"}
    assert len(out["world_facts"]) == 151 and out["world_facts"][0].startswith("设定0"), "最早的设定不能丢"
    assert any("新主线伏笔" in n for n in notes)


def test_select_for_context_keeps_major_and_relevant_old_items():
    data = mem.normalize_memory({
        "foreshadowing": [{"description": f"路人甲乙丙第{i}次闲聊里提到的琐事{i}", "planted_chapter": i, "status": "open"}
                          for i in range(1, 101)]
        + [{"description": "城墙根碎瓦片上的平州府衙关防印纹", "planted_chapter": 2, "status": "open"},
           {"description": "沈家地窖里的樟木箱子装着什么", "planted_chapter": 1, "status": "open", "major": 1}],
        "world_facts": [f"边镇杂事{i}" for i in range(100)] + ["平州府衙关防印由刑名师爷保管"],
    })
    # 把早期那条相关伏笔挪到最前面，确认它不是靠"最近"入选
    data["foreshadowing"].insert(0, data["foreshadowing"].pop(100))
    text = mem.render_structured(mem.select_for_context(data, "本章：沈砚拿着碎瓦片去平州府衙核对关防印纹"))
    assert "【主线】沈家地窖里的樟木箱子" in text, "主线伏笔无论多早都要带上"
    assert "平州府衙关防印纹" in text, "和本章相关的早期伏笔要召回"
    assert "路人甲乙丙第1次闲聊" not in text, "无关的早期次要伏笔不带"
    assert "未列出" in text
    assert "平州府衙关防印由刑名师爷保管" in text
    full = mem.render_structured(data)
    assert len(text) < len(full) / 2


def test_reasoning_effort_by_task_type(S):
    from app.models import AiModel, AiProvider
    from app.services.ai_client import AiClient, output_limit
    db = S()
    ds = AiProvider(name="ds", base_url="https://api.deepseek.com", api_key="")
    other = AiProvider(name="o", base_url="https://api.example.com/v1", api_key="")
    model = AiModel(provider_id=1, name="deepseek-flash", max_tokens=393216)
    plain = AiModel(provider_id=2, name="gpt-x", max_tokens=0)
    c = AiClient(db)
    msgs = [{"role": "user", "content": "hi"}]

    def payload(provider, m, task_type, max_tokens=4096):
        opts = c._reasoning_options(provider, m, task_type, {"task_type": task_type, "max_tokens": max_tokens})
        return c._build_payload(msgs, m, opts, stream=False)

    p = payload(ds, model, "update_memory", 6144)
    assert p["reasoning_effort"] == "none" and p["max_tokens"] == 6144, "辅助任务默认不思考，也不需要推理余量"
    p = payload(ds, model, "consistency_check")
    assert p["reasoning_effort"] == "low" and p["max_tokens"] > 4096 + 16000
    assert payload(ds, model, "continue_chapter")["reasoning_effort"] == "high"
    assert "reasoning_effort" not in payload(other, model.__class__(provider_id=2, name="gpt-x"), "update_memory")
    _config(S, reasoning_auxiliary="low", reasoning_writing="off")
    db.expire_all()
    assert payload(ds, model, "generate_summary")["reasoning_effort"] == "low"
    assert "reasoning_effort" not in payload(ds, model, "continue_chapter"), "off=不传"
    assert output_limit(plain, {"max_tokens": 100, "_reasoning": "none"}) == 100
    db.close()
