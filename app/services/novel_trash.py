"""小说回收站：删除先移入回收站（软删除），可恢复；彻底删除才真正清掉数据；超过保留天数自动清理"""
from datetime import timedelta

from sqlalchemy.orm import Session

from app.helpers import now
from app.models import (
    AiTask, ArcSummary, Chapter, CharacterProfile, ConsistencyReport, Idea, Novel, NovelAutomation, NovelMemory,
    NovelSetting, StoryTerm,
)
from app.services import chapter_versions, memory_store
from app.services.ai_client import config_value

DEFAULT_RETENTION_DAYS = 30


def retention_days(db: Session) -> int:
    try:
        return max(1, int(float(config_value(db, "trash_retention_days", DEFAULT_RETENTION_DAYS))))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def purge(db: Session, novel: Novel) -> None:
    """彻底删除一本小说及其全部数据（不提交）"""
    nid = novel.id
    db.query(Chapter).filter(Chapter.novel_id == nid).delete(synchronize_session=False)
    db.query(NovelSetting).filter(NovelSetting.novel_id == nid).delete(synchronize_session=False)
    db.query(NovelMemory).filter(NovelMemory.novel_id == nid).delete(synchronize_session=False)
    db.query(ConsistencyReport).filter(ConsistencyReport.novel_id == nid).delete(synchronize_session=False)
    db.query(NovelAutomation).filter(NovelAutomation.novel_id == nid).delete(synchronize_session=False)
    for model in (CharacterProfile, StoryTerm, ArcSummary):
        db.query(model).filter(model.novel_id == nid).delete(synchronize_session=False)
    memory_store.delete_snapshots(db, nid)
    chapter_versions.delete_versions(db, nid)
    db.query(AiTask).filter(AiTask.ref_id == nid, AiTask.ref_type == "novel").delete(synchronize_session=False)
    db.query(Idea).filter(Idea.novel_id == nid).update({"novel_id": None}, synchronize_session=False)
    # 小说本身也用批量删除：db.delete(novel) 会按关系把已加载的设定/记忆的 novel_id 置空，
    # 而它们刚被批量删掉，UPDATE 匹配不到行会报 StaleDataError
    db.expunge(novel)
    db.query(Novel).filter(Novel.id == nid).delete(synchronize_session=False)


def purge_expired(db: Session) -> int:
    """清理回收站里超过保留天数的小说，返回清理数量"""
    cutoff = now() - timedelta(days=retention_days(db))
    expired = db.query(Novel).filter(Novel.deleted_at.isnot(None), Novel.deleted_at < cutoff).all()
    for novel in expired:
        purge(db, novel)
    if expired:
        db.commit()
    return len(expired)
