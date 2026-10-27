"""上下文组装：长度预算、模型窗口、检索正文片段"""
from app.models import AiModel, AiProvider, Novel, SystemConfig
from app.services import context_builder, retrieval
from conftest import add_chapter, make_novel


def test_budget_shrinks_recent_chapters_first(S):
    make_novel(S, 1)
    for no in range(1, 5):
        add_chapter(S, 1, no, f"正文ch{no}" + "字" * 40)
    db = S()
    novel = db.get(Novel, 1)
    full = context_builder.build_chapter_context(db, novel, 5, "", [], "记忆")
    assert full.count("正文ch") == 4
    db.add(SystemConfig(key="context_max_chars", value="200"))
    db.commit()
    small = context_builder.build_chapter_context(db, novel, 5, "", [], "记忆")
    assert small.count("正文ch") == 1 and "正文ch4" in small and "【本章大纲】" in small


def test_budget_from_model_context_window(S):
    db = S()
    p = AiProvider(name="p", base_url="x", api_key="", status=1, is_default=1)
    db.add(p)
    db.commit()
    db.add(AiModel(provider_id=p.id, name="m", display_name="m", context_window=16000, status=1, is_default=1))
    db.commit()
    assert context_builder.context_budget(db, output_tokens=6000) == 8000
    db.add(SystemConfig(key="context_max_chars", value="5000"))
    db.commit()
    assert context_builder.context_budget(db, output_tokens=6000) == 5000, "取较小值"


def test_retrieval_finds_detail_in_body_text(S):
    make_novel(S, 5)
    for i in range(1, 12):
        body = "日常修炼。" * 30 + ("他在后山捡到一枚刻着饕餮纹的玉佩。" if i == 2 else "")
        add_chapter(S, 5, i, body, summary="修炼日常")
    db = S()
    res = retrieval.related_chapters(db, db.get(Novel, 5), 12, "饕餮纹玉佩再次出现")
    assert res and res[0]["chapter_no"] == 2 and "饕餮" in res[0]["excerpt"]
    assert "相关原文片段" in retrieval.render_retrieved(res)
