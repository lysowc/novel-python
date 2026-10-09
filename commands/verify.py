"""回归自测：python -m commands.verify

验证三大升级：检索式上下文 / 结构化记忆 v2 / 一致性审校规整（自动造数并清理）。
"""
import json
import sys
import time

from sqlalchemy import func

from app.db import SessionLocal
from app.helpers import now, word_count
from app.models import AiTask, Chapter, Novel, NovelMemory, SystemConfig
from app.services import context_builder, memory as memory_service, retrieval, task_service
from app.services.ai_service import normalize_report


class Checker:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, msg: str, cond: bool) -> None:
        if cond:
            self.passed += 1
            print(f"  ✔ {msg}")
        else:
            self.failed += 1
            print(f"  ✘ {msg}")


def main() -> int:
    ck = Checker()
    db = SessionLocal()
    try:
        # 1. 造数：10 章，第 3 章是"古戒"关键章节
        novel = Novel(
            title=f"自测小说_{int(time.time())}", description="自测用", status="draft",
            is_public=0, word_count=0, chapter_count=0, created_at=now(), updated_at=now(),
        )
        db.add(novel)
        db.commit()
        novel_id = novel.id

        key_chapter = "林凡在试炼中得到神秘古戒，戒中出现残魂低语，暗示古戒与天元秘境有关。"
        for n in range(1, 11):
            db.add(Chapter(
                novel_id=novel_id, chapter_no=n, title=f"第{n}章 测试章节",
                summary=key_chapter if n == 3 else f"林凡修炼突破，第{n}次小比获胜，结识新的同门，宗门日常平静推进。",
                content="正文内容占位。" + "字" * 500, word_count=500, status="published",
                created_at=now(), updated_at=now(),
            ))
        db.commit()
        ck.check("造数：10 章已建", db.query(Chapter).filter(Chapter.novel_id == novel_id).count() == 10)

        # 2. 临时配置：缩小滚动窗口，让第 3 章必然被"最近"策略丢弃
        orig = {}
        for key in ("context_summary_max_chars", "context_max_recent_chapters",
                    "retrieval_enabled", "retrieval_max_chapters"):
            row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
            orig[key] = row.value if row else None
            if row:
                row.value = {"context_summary_max_chars": "150", "context_max_recent_chapters": "5",
                             "retrieval_enabled": "1", "retrieval_max_chapters": "3"}[key]
            else:
                db.add(SystemConfig(key=key, value={"context_summary_max_chars": "150",
                                                    "context_max_recent_chapters": "5",
                                                    "retrieval_enabled": "1",
                                                    "retrieval_max_chapters": "3"}[key]))
        db.commit()

        novel.outline = json.dumps({"volumes": [{
            "title": "第一卷",
            "chapters": [{"no": 11, "title": "第11章 古戒苏醒",
                          "summary": "古戒中的残魂彻底苏醒，与林凡对话并揭示天元秘境的秘密。"}],
        }]}, ensure_ascii=False)
        db.commit()

        window = context_builder.summaries_text(db, novel)
        ck.check("滚动窗口不含第 3 章（设计前提成立）", "第3章" not in window)

        retrieved = retrieval.related_chapters(db, novel, 11, "")
        top_no = retrieved[0]["chapter_no"] if retrieved else 0
        ck.check("检索召回第 3 章（古戒章节）", top_no == 3)
        ck.check("检索结果不含已在上下文的近章", all(r["chapter_no"] <= 5 for r in retrieved))

        ctx = context_builder.build_chapter_context(db, novel, 11, "", retrieved)
        ck.check("生成上下文含【相关历史章节（检索召回）】", "【相关历史章节（检索召回）】" in ctx)
        ck.check("上下文含召回内容", "第3章" in ctx)

        # 3. 旧格式记忆渲染 + v2 规整
        legacy = NovelMemory(
            novel_id=novel_id,
            content=json.dumps({"current_location": "青云宗",
                                "main_character": {"name": "林凡"},
                                "foreshadowing": ["残魂苏醒"]}, ensure_ascii=False),
        )
        db.add(legacy)
        db.commit()
        text = memory_service.to_context_text(legacy.content)
        ck.check("旧格式记忆可渲染", "current_location" in text and "残魂苏醒" in text)

        normalized = memory_service.normalize_memory({
            "current_location": "青云宗",
            "main_character": {"name": "林凡", "realm": "炼气九层"},
            "foreshadowing": [
                {"description": "残魂苏醒", "status": "open"},
                "血魔宗觊觎古戒",
            ],
            "important_items": ["上古青铜戒"],
        })
        ck.check("normalizeMemory 产出 v2 schema", normalized.get("schema") == "v2")
        ck.check("normalizeMemory 迁移旧人物字段", normalized["characters"][0]["name"] == "林凡")
        ck.check("normalizeMemory 规整伏笔条目",
                 len(normalized["foreshadowing"]) == 2 and normalized["foreshadowing"][0]["status"] == "open")
        ck.check("normalizeMemory 规整物品条目", normalized["important_items"][0]["name"] == "上古青铜戒")

        legacy.content = json.dumps(normalized, ensure_ascii=False)
        db.commit()
        v2text = memory_service.to_context_text(legacy.content)
        ck.check("v2 记忆结构化渲染",
                 "【当前状态】" in v2text and "【未回收伏笔】" in v2text and "残魂苏醒" in v2text)

        # 4. 审校报告规整
        report = normalize_report({
            "status": "warning",
            "summary": "总体正常",
            "issues": [
                {"severity": "major", "type": "foreshadowing_dropped",
                 "description": "伏笔遗忘", "suggestion": "重新激活", "related_chapters": [3, "5"]},
                {"severity": "critical", "description": "无 type 兜底"},
            ],
        })
        ck.check("normalizeReport 保留 2 个问题", len(report["issues"]) == 2)
        ck.check("normalizeReport 章号过滤为 int", report["issues"][0]["related_chapters"] == [3, 5])
        ck.check("normalizeReport 空输入返回 None", normalize_report({}) is None)

        # 5. 内部入队（无进行中任务检查）
        task = task_service.enqueue(db, "consistency_check", novel_id, {})
        ck.check("enqueue 创建审校任务",
                 task.id > 0 and db.query(AiTask).filter(AiTask.id == task.id).first().status == "pending")

        # 6. 审校进度文本（抽样）
        progress = context_builder.written_progress_text(db, novel, 600)
        ck.check("审校进度文本抽样", "第10章" in progress and len(progress) <= 700)

        # 清理
        db.query(AiTask).filter(AiTask.ref_id == novel_id).delete()
        db.query(Chapter).filter(Chapter.novel_id == novel_id).delete()
        db.query(NovelMemory).filter(NovelMemory.novel_id == novel_id).delete()
        db.query(Novel).filter(Novel.id == novel_id).delete()
        for key, value in orig.items():
            row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
            if row:
                row.value = value
            else:
                db.delete(db.query(SystemConfig).filter(SystemConfig.key == key).first())
        db.commit()
    finally:
        db.close()

    print(f"\n自测结束：通过 {ck.passed} 项 / 失败 {ck.failed} 项（测试数据已清理）")
    return 0 if ck.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
