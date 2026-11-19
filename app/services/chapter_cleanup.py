"""删除章节后的善后：记忆回退、资料库撤回、分段总结作废

以前删章节只删正文和那一章的记忆快照，全书最新记忆、人物档案、名词表、分段总结里
还留着被删章节的内容，重写时旧剧情会混进新章节。现在：
- 删的是最后几章（尾部）：记忆退回到剩下最后一章结束时的快照；这些章节建的档案、名词、
  境界变化撤回（作者锁定的保留）；覆盖到这些章节的分段总结删掉
- 全部删光：记忆清空，资料库里 AI 建的条目清空（锁定的保留）
- 删的是中间章节：后面章节的记忆建立在被删章节之上，不自动改，提示从第几章重建
"""
from sqlalchemy.orm import Session

from app.helpers import now
from app.models import ArcSummary, Chapter, Novel, NovelMemory, NovelMemorySnapshot
from app.services import chapter_versions, memory_store, story_bible


def delete_chapters(db: Session, novel: Novel, chapters: list[Chapter]) -> dict:
    """删除章节并善后（会提交）。返回给前端的说明"""
    nos = sorted({int(c.chapter_no) for c in chapters})
    if not nos:
        return {"deleted": 0}
    for c in chapters:
        chapter_versions.save_version(db, c, "delete")  # 可在「已删除章节」里恢复
        memory_store.delete_snapshots(db, novel.id, c.chapter_no)
        db.delete(c)
    db.flush()

    remaining = db.query(Chapter.chapter_no).filter(Chapter.novel_id == novel.id).all()
    remaining_max = max((int(r[0]) for r in remaining), default=0)
    result: dict = {"deleted": len(nos), "remaining": len(remaining), "memory_rolled_back_to": None,
                    "rebuild_from": 0, "all_deleted": not remaining}

    if nos[0] > remaining_max:
        # 删的是尾部：记忆和资料库都退回到剩下最后一章结束时
        snap = memory_store.get_snapshot(db, novel.id, remaining_max) if remaining_max else None
        memory = db.query(NovelMemory).filter(NovelMemory.novel_id == novel.id).first()
        if memory is not None:
            if snap is not None:
                memory.content = snap.content
                memory.updated_at = now()
            else:
                db.delete(memory)
        db.query(NovelMemorySnapshot).filter(NovelMemorySnapshot.novel_id == novel.id,
                                             NovelMemorySnapshot.chapter_no > remaining_max) \
            .delete(synchronize_session=False)
        story_bible.reset_from(db, novel.id, remaining_max + 1)
        db.query(ArcSummary).filter(ArcSummary.novel_id == novel.id, ArcSummary.end_no > remaining_max) \
            .delete(synchronize_session=False)
        result["memory_rolled_back_to"] = remaining_max
    else:
        # 删的是中间章节：后面章节的记忆还是按被删内容算的
        result["rebuild_from"] = nos[0]
        for no in nos:
            story_bible.invalidate_arc(db, novel.id, no)

    db.commit()
    novel.word_count = int(sum(c.word_count or 0 for c in db.query(Chapter).filter(Chapter.novel_id == novel.id)))
    novel.chapter_count = len(remaining)
    db.commit()
    return result


def clear_memory(db: Session, novel_id: int) -> None:
    """清空全书记忆和全部记忆快照（会提交）"""
    db.query(NovelMemory).filter(NovelMemory.novel_id == novel_id).delete(synchronize_session=False)
    memory_store.delete_snapshots(db, novel_id)
    db.commit()
