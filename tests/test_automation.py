"""定时续写、费用上限、模型分工、大纲续写、质量检查、流式读取与首字前取消"""
import json
import time
import types
from datetime import date, datetime

import httpx
import pytest

from app.helpers import now
from app.models import AiLog, AiModel, AiProvider, AiTask, Chapter, Novel, NovelAutomation, SystemConfig
from app.services import ai_client, budget, scheduler, task_service as ts
from conftest import add_chapter, make_novel, run_task
from test_api import client  # noqa: F401  复用登录好的 TestClient


def set_config(S, **values):
    db = S()
    for key, value in values.items():
        row = db.query(SystemConfig).filter_by(key=key).first()
        if row:
            row.value = str(value)
        else:
            db.add(SystemConfig(key=key, value=str(value)))
    db.commit()
    db.close()


def add_model(S, name, price_in=0, price_out=0, default=0):
    db = S()
    p = AiProvider(name=f"p-{name}", base_url="http://ai.test", api_key="k", status=1, is_default=default)
    db.add(p)
    db.commit()
    m = AiModel(provider_id=p.id, name=name, display_name=name, price_input=price_in, price_output=price_out,
                status=1, is_default=default)
    db.add(m)
    db.commit()
    mid = m.id
    db.close()
    return mid


# ---------- 模型分工 ----------

def test_model_routing_by_task_type(S):
    add_model(S, "default-model", default=1)
    writing = add_model(S, "writer")
    aux = add_model(S, "cheap")
    db = S()
    assert ai_client.resolve_target(db, "continue_chapter")[1].name == "default-model"
    set_config(S, model_writing_id=writing, model_auxiliary_id=aux)
    db = S()
    assert ai_client.resolve_target(db, "continue_chapter")[1].name == "writer"
    assert ai_client.resolve_target(db, "generate_summary")[1].name == "cheap"
    assert ai_client.resolve_target(db, "update_memory")[1].name == "cheap"
    assert ai_client.resolve_target(db, "idea_chat")[1].name == "writer"
    db.get(AiModel, aux).status = 0  # 停用后退回默认
    db.commit()
    assert ai_client.resolve_target(db, "generate_summary")[1].name == "default-model"


# ---------- 费用上限 ----------

def _spend(S, novel_id, cost):
    db = S()
    db.add(AiLog(novel_id=novel_id, provider="p", model="m", task_type="continue_chapter", prompt="",
                 prompt_tokens=0, completion_tokens=0, total_tokens=0, cost=cost, usage_estimated=0,
                 duration=0, status="success", error_message="", created_at=now()))
    db.commit()


def test_budget_blocks_and_stops_chain(S, fake_ai):
    make_novel(S, 1)
    make_novel(S, 2)
    set_config(S, budget_novel_daily=1)
    _spend(S, 1, 1.5)
    task = run_task(S, "continue_chapter", 1, remaining=5)
    assert task.status == "failed" and "费用已达上限" in task.error_message and task.params.get("chain_stopped")
    assert S().query(Chapter).count() == 0
    assert run_task(S, "continue_chapter", 2).status == "success", "其他小说不受单本上限影响"
    set_config(S, budget_daily=1)
    assert "今日 AI 费用已达上限" in budget.block_reason(S(), 2)


# ---------- 定时续写 ----------

def test_scheduler_runs_once_per_day_after_time(S):
    make_novel(S, 1)
    db = S()
    db.add(NovelAutomation(novel_id=1, schedule_enabled=1, schedule_time="08:00", chapters_per_run=3,
                           target_words=2000, last_run_message="", auto_extend_outline=0, extend_chapters=10))
    db.commit()
    assert scheduler.run_schedules(S(), datetime(2026, 10, 28, 7, 59)) == [], "没到点不执行"
    notes = scheduler.run_schedules(S(), datetime(2026, 10, 28, 8, 0))
    assert len(notes) == 1 and "已安排续写 3 章" in notes[0]
    task = S().query(AiTask).one()
    assert task.task_type == "continue_chapter" and task.params == {"remaining": 2, "target_words": 2000}
    db = S()
    db.query(AiTask).update({"status": "success"})
    db.commit()
    assert scheduler.run_schedules(S(), datetime(2026, 10, 28, 20, 0)) == [], "同一天只执行一次"
    assert len(scheduler.run_schedules(S(), datetime(2026, 10, 29, 8, 5))) == 1, "第二天再执行"


def test_scheduler_waits_when_busy_and_skips_over_budget(S):
    make_novel(S, 1)
    db = S()
    db.add(NovelAutomation(novel_id=1, schedule_enabled=1, schedule_time="08:00", chapters_per_run=1,
                           target_words=0, last_run_message="", auto_extend_outline=0, extend_chapters=10))
    db.add(AiTask(task_type="generate_outline", ref_id=1, params={}, status="running", error_message=""))
    db.commit()
    notes = scheduler.run_schedules(S(), datetime(2026, 10, 28, 9, 0))
    assert "等待" in notes[0] and S().get(NovelAutomation, 1).last_run_date is None, "忙时下一分钟再试"
    db = S()
    db.query(AiTask).update({"status": "success"})
    db.commit()
    set_config(S, budget_daily=0.5)
    _spend(S, 1, 1)
    notes = scheduler.run_schedules(S(), datetime(2026, 10, 28, 9, 1))
    assert "跳过" in notes[0] and S().get(NovelAutomation, 1).last_run_date == date(2026, 10, 28)
    assert S().query(AiTask).filter_by(status="pending").count() == 0


def test_scheduler_tick_uses_redis_lock(S, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "run_schedules", lambda db, current: calls.append(1) or [])
    scheduler.tick()
    scheduler.tick()
    assert len(calls) == 1, "同一分钟只跑一次"


# ---------- 大纲续写 ----------

def _outline(n):
    return json.dumps({"volumes": [{"title": "第一卷", "chapters": [
        {"no": i, "title": f"t{i}", "summary": ""} for i in range(1, n + 1)]}]}, ensure_ascii=False)


def test_auto_extend_outline_before_writing(S, fake_ai):
    make_novel(S, 1, outline=_outline(4))
    for no in (1, 2):
        add_chapter(S, 1, no, f"x{no}")
    db = S()
    db.add(NovelAutomation(novel_id=1, schedule_enabled=0, schedule_time="08:00", chapters_per_run=1,
                           target_words=0, last_run_message="", auto_extend_outline=1, extend_chapters=3))
    db.commit()
    assert run_task(S, "continue_chapter", 1).status == "success"  # 写第 3 章：剩 2 章 ≤ 3，先续写大纲
    outline = S().get(Novel, 1).get_outline_array()
    nos = [c["no"] for v in outline["volumes"] for c in v["chapters"]]
    assert nos == [1, 2, 3, 4, 5, 6, 7], "新章节从 5 开始重新编号"
    assert outline["volumes"][-1]["title"] == "第二卷 新篇"


def test_manual_extend_outline_task(S, fake_ai):
    make_novel(S, 1, outline=_outline(2))
    task = run_task(S, "extend_outline", 1, chapters=3)
    assert task.status == "success"
    assert len([c for v in S().get(Novel, 1).get_outline_array()["volumes"] for c in v["chapters"]]) == 5


# ---------- 质量检查 ----------

def test_quality_check_retries_once_and_keeps_better(S, fake_ai):
    set_config(S, quality_check_enabled=1, chapter_target_words=100)
    make_novel(S, 1)
    good = "林凡拔剑出鞘，剑光如虹。" * 12
    fake_ai.stream_texts = ["太短了。", good]
    task = run_task(S, "continue_chapter", 1)
    assert task.status == "success" and "quality_warning" not in task.params
    assert S().query(Chapter).one().content == good
    events = [json.loads(e) for e in ts.connect_redis().lrange(f"ai:stream:{task.id}", 0, -1)]
    assert any(e["type"] == "reset" for e in events), "重写前通知前端清空"
    assert any(e.get("stage") == "quality_retry" for e in events)


def test_quality_check_keeps_best_and_warns(S, fake_ai):
    set_config(S, quality_check_enabled=1, chapter_target_words=100)
    make_novel(S, 1)
    first = "林凡拔剑出鞘，剑光如虹。" * 6  # 字数够，但被截断
    fake_ai.stream_texts = [first, "短。"]
    fake_ai.finish_reasons = ["length", "stop"]
    task = run_task(S, "continue_chapter", 1)
    assert task.status == "success"
    assert S().query(Chapter).one().content == first, "两版都有问题时保留较好的一版"
    assert "截断" in task.params["quality_warning"]


# ---------- AiClient 流式读取 ----------

def _sse(*events, done=True):
    lines = [f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events]
    if done:
        lines.append("data: [DONE]\n\n")
    return lines


class SlowStream(httpx.SyncByteStream):
    def __init__(self, chunks, first_delay=0.0):
        self.chunks, self.first_delay = chunks, first_delay

    def __iter__(self):
        time.sleep(self.first_delay)
        for c in self.chunks:
            yield c.encode()


def _patch_http(monkeypatch, handler):
    real = httpx.Client
    fake = types.SimpleNamespace(Client=lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
                                 Timeout=httpx.Timeout)
    monkeypatch.setattr(ai_client, "httpx", fake)


def test_ai_client_stream_parses_text_usage_finish(S, monkeypatch):
    add_model(S, "m", price_in=1, price_out=2, default=1)
    chunks = _sse({"choices": [{"delta": {"content": "你好"}}]},
                  {"choices": [{"delta": {"content": "世界"}, "finish_reason": "stop"}]},
                  {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}})
    _patch_http(monkeypatch, lambda request: httpx.Response(200, stream=SlowStream(chunks)))
    got = []
    client = ai_client.AiClient(S(), novel_id=7)
    result = client.chat_stream([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                                {"task_type": "continue_chapter"}, on_delta=got.append)
    assert result["text"] == "你好世界" and got == ["你好", "世界"]
    assert result["finish_reason"] == "stop" and result["done"] is True
    log = S().query(AiLog).one()
    assert log.novel_id == 7 and log.total_tokens == 14 and float(log.cost) == pytest.approx(18 / 1e6)


def test_ai_client_stream_cancel_before_first_token(S, monkeypatch):
    add_model(S, "m", default=1)
    chunks = _sse({"choices": [{"delta": {"content": "迟到的字"}}]})
    _patch_http(monkeypatch, lambda request: httpx.Response(200, stream=SlowStream(chunks, first_delay=5)))
    client = ai_client.AiClient(S())
    cancel_at = time.time() + 0.6

    def cancel_check():
        if time.time() > cancel_at:
            raise ts.TaskCancelled()
    client.cancel_check = cancel_check
    started = time.time()
    with pytest.raises(ts.TaskCancelled):
        client.chat_stream([{"role": "user", "content": "u"}], {"task_type": "continue_chapter"})
    assert time.time() - started < 2.5, "首字前也能在 1 秒左右取消"
    assert S().query(AiLog).one().error_message == "已取消"


def test_ai_client_stream_retries_without_stream_options(S, monkeypatch):
    add_model(S, "m", default=1)
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append("stream_options" in body)
        if "stream_options" in body:
            return httpx.Response(400, json={"error": {"message": "unknown field stream_options"}})
        return httpx.Response(200, stream=SlowStream(_sse({"choices": [{"delta": {"content": "好"}}]})))
    _patch_http(monkeypatch, handler)
    result = ai_client.AiClient(S()).chat_stream([{"role": "user", "content": "u"}], {"task_type": "x"})
    assert result["text"] == "好" and seen == [True, False]


# ---------- 思考型模型（推理过程占输出额度） ----------

def test_output_limit_adds_reasoning_headroom_capped_by_model():
    m = AiModel(name="m", max_tokens=None)
    assert ai_client.output_limit(m, {"max_tokens": 6000}) == 6000 + ai_client.REASONING_HEADROOM
    assert ai_client.output_limit(m, {}) == ai_client.DEFAULT_MAX_TOKENS
    m.max_tokens = 8192
    assert ai_client.output_limit(m, {"max_tokens": 6000}) == 8192, "不超过模型上限"
    assert ai_client.output_limit(m, {"max_tokens": 6000, "_no_headroom": True}) == 6000


def test_stream_reasoning_only_reports_clear_error_and_thinking_stage(S, monkeypatch):
    add_model(S, "m", default=1)
    chunks = _sse({"choices": [{"delta": {"content": None, "reasoning_content": "想一想"}}]},
                  {"choices": [{"delta": {"content": None, "reasoning_content": "再想想"}, "finish_reason": "length"}]})
    _patch_http(monkeypatch, lambda request: httpx.Response(200, stream=SlowStream(chunks)))
    stages = []
    with pytest.raises(RuntimeError, match="思考上"):
        ai_client.AiClient(S()).chat_stream([{"role": "user", "content": "u"}], {"task_type": "continue_chapter"},
                                            on_stage=lambda st, msg="": stages.append(st))
    assert stages.count("thinking") == 1


def test_chat_null_content_and_max_tokens_rejected_retry(S, monkeypatch):
    add_model(S, "m", default=1)
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body["max_tokens"])
        if body["max_tokens"] > 4096:
            return httpx.Response(400, json={"error": {"message": "max_tokens is too large: must be <= 4096"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": None}, "finish_reason": "length"}]})
    _patch_http(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="思考上"):
        ai_client.AiClient(S()).chat([{"role": "user", "content": "u"}], {"task_type": "x", "max_tokens": 4000})
    assert seen == [4000 + ai_client.REASONING_HEADROOM, 4000], "超限时去掉余量重试一次"


# ---------- AI 日志记录参数和完整输入输出 ----------

def test_ai_log_records_params_input_output_reasoning(S, monkeypatch):
    add_model(S, "m", default=1)
    chunks = _sse({"choices": [{"delta": {"content": None, "reasoning_content": "先想想沈砚"}}]},
                  {"choices": [{"delta": {"content": "沈砚推门"}}]},
                  {"choices": [{"delta": {"content": "而入"}, "finish_reason": "stop"}]})
    _patch_http(monkeypatch, lambda request: httpx.Response(200, stream=SlowStream(chunks)))
    ai_client.AiClient(S(), novel_id=3).chat_stream(
        [{"role": "system", "content": "你是作家"}, {"role": "user", "content": "写第5章：雁回镇"}],
        {"task_type": "continue_chapter", "max_tokens": 3000, "temperature": 1.2})
    _patch_http(monkeypatch, lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": "{\"ok\": 1}", "reasoning_content": "整理一下"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3}}))
    ai_client.AiClient(S(), novel_id=3).chat(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "第一问"},
         {"role": "assistant", "content": "答"}, {"role": "user", "content": "第二问"}], {"task_type": "update_memory"})
    a, b = S().query(AiLog).order_by(AiLog.id).all()
    assert a.input_text == "写第5章：雁回镇" and a.output_text == "沈砚推门而入" and a.reasoning_text == "先想想沈砚"
    assert float(a.temperature) == 1.2 and a.max_tokens >= 3000 and a.prompt == "你是作家"
    assert b.output_text == '{"ok": 1}' and b.reasoning_text == "整理一下"
    assert b.input_text == "[user]\n第一问\n\n[assistant]\n答\n\n[user]\n第二问", "system 单独记在 prompt，不重复记"


def test_ai_logs_search_and_detail(S, client):
    db = S()
    db.add_all([
        AiLog(novel_id=3, task_type="continue_chapter", status="success", prompt="p", input_text="大纲：樟木箱子",
              output_text="沈砚" + "很长的正文" * 50 + "撬开了樟木箱子，里头是一本账册", reasoning="none", temperature=1.0),
        AiLog(novel_id=4, task_type="update_memory", status="failed", input_text="别的", output_text="无关"),
    ])
    db.commit()
    db.close()
    data = client.get("/api/admin/ai/logs", params={"keyword": "樟木箱子"}).json()["data"]
    assert data["total"] == 1
    row = data["list"][0]
    assert row["match"]["field"] == "output_text" and "撬开了樟木箱子" in row["match"]["text"]
    assert "output_text" not in row and row["has_io"] == 1 and row["reasoning"] == "none" and row["temperature"] == 1.0
    assert client.get("/api/admin/ai/logs", params={"novel_id": 4, "status": "failed"}).json()["data"]["total"] == 1
    assert client.get("/api/admin/ai/logs", params={"task_type": "update_memory"}).json()["data"]["total"] == 1
    detail = client.get(f"/api/admin/ai/logs/{row['id']}").json()["data"]
    assert detail["output_text"].endswith("里头是一本账册") and detail["input_text"] == "大纲：樟木箱子"
