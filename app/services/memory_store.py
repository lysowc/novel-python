"""小说记忆的按章快照与"某章应该看到的记忆"

小说记忆（novel_memory）是全书最新状态；快照（novel_memory_snapshot）是"第 N 章结束时"的状态。
生成或重写第 N 章时应该基于"第 N 章开始前"的记忆，而不是全书最新记忆——
否则重写中间章节会看到后面的剧情，重写最后一章会把旧版本的内容再叠加一次。
"""
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.helpers import now
from app.models import Chapter, Novel, NovelMemory, NovelMemorySnapshot
from app.services import memory as memory_service

# source 取值
CURRENT = "current"              # 写新章节：全书最新记忆就是"本章开始前"的状态
SNAPSHOT = "snapshot"            # 有上一章的快照
EMPTY = "empty"                  # 本章之前没有章节
CURRENT_STALE = "current_stale"  # 重写已有章节但没有快照（旧数据）：只能用最新记忆，可能含本章及之后的内容

STALE_NOTE = "（注意：没有本章开始前的记忆快照，以下是全书最新进度的记忆，可能包含本章及之后的剧情。" \
             "请只把它当作人物与设定参考，不要提前写出后续情节。）"


@dataclass
class MemoryBase:
    content: str
    source: str
    snapshot_no: int = 0

    def context_text(self) -> str:
        if not (self.content or "").strip():
            return "（暂无小说记忆）"
        text = memory_service.to_context_text(self.content)
        if self.source == CURRENT_STALE:
            return STALE_NOTE + "\n" + text
        return text


def current_content(db: Session, novel_id: int) -> str:
    row = db.query(NovelMemory).filter(NovelMemory.novel_id == novel_id).first()
    return (row.content or "") if row else ""


def max_chapter_no(db: Session, novel_id: int) -> int:
    return int(db.query(func.max(Chapter.chapter_no)).filter(Chapter.novel_id == novel_id).scalar() or 0)


def get_snapshot(db: Session, novel_id: int, chapter_no: int) -> NovelMemorySnapshot | None:
    return db.query(NovelMemorySnapshot).filter(
        NovelMemorySnapshot.novel_id == novel_id, NovelMemorySnapshot.chapter_no == chapter_no).first()


def snapshot_before(db: Session, novel_id: int, chapter_no: int) -> NovelMemorySnapshot | None:
    return (
        db.query(NovelMemorySnapshot)
        .filter(NovelMemorySnapshot.novel_id == novel_id, NovelMemorySnapshot.chapter_no < chapter_no)
        .order_by(NovelMemorySnapshot.chapter_no.desc())
        .first()
    )


def save_snapshot(db: Session, novel_id: int, chapter_no: int, content: str) -> None:
    """写入/覆盖第 chapter_no 章的快照（不提交）"""
    if chapter_no <= 0:
        return
    row = get_snapshot(db, novel_id, chapter_no)
    if not row:
        row = NovelMemorySnapshot(novel_id=novel_id, chapter_no=chapter_no, created_at=now())
        db.add(row)
    row.content = content
    row.updated_at = now()


def delete_snapshots(db: Session, novel_id: int, chapter_no: int | None = None) -> None:
    """删除快照（不提交）：指定章号删一章，否则删整本"""
    q = db.query(NovelMemorySnapshot).filter(NovelMemorySnapshot.novel_id == novel_id)
    if chapter_no is not None:
        q = q.filter(NovelMemorySnapshot.chapter_no == chapter_no)
    q.delete(synchronize_session=False)


def memory_before(db: Session, novel: Novel, chapter_no: int) -> MemoryBase:
    """第 chapter_no 章"开始前"应该看到的记忆"""
    exists = db.query(Chapter.id).filter(
        Chapter.novel_id == novel.id, Chapter.chapter_no == chapter_no).first() is not None
    later = db.query(Chapter.id).filter(
        Chapter.novel_id == novel.id, Chapter.chapter_no > chapter_no).first() is not None

    # 写全新的最后一章：最新记忆就是开始前的状态（也包含用户在后台手动改过的记忆）
    if not exists and not later:
        return MemoryBase(current_content(db, novel.id), CURRENT)

    snap = snapshot_before(db, novel.id, chapter_no)
    if snap:
        return MemoryBase(snap.content or "", SNAPSHOT, snap.chapter_no)

    earlier = db.query(Chapter.id).filter(
        Chapter.novel_id == novel.id, Chapter.chapter_no < chapter_no).first() is not None
    if not earlier:
        return MemoryBase("", EMPTY)
    return MemoryBase(current_content(db, novel.id), CURRENT_STALE)
