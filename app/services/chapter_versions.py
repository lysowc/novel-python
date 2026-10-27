"""章节历史版本：任何覆盖或删除章节正文的操作前，先把旧内容存一份"""
from sqlalchemy.orm import Session

from app.helpers import now
from app.models import Chapter, ChapterVersion

MAX_VERSIONS_PER_CHAPTER = 20


def save_version(db: Session, chapter: Chapter, source: str) -> ChapterVersion | None:
    """保存 chapter 当前内容为一个历史版本（不提交）；空正文不保存。超过上限时删除最旧的。"""
    if not (chapter.content or "").strip():
        return None
    latest = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.novel_id == chapter.novel_id, ChapterVersion.chapter_no == chapter.chapter_no)
        .order_by(ChapterVersion.id.desc())
        .first()
    )
    # 和最近一个版本完全相同就不重复存（例如连续两次保存未修改的正文）
    if latest and latest.content == chapter.content and latest.title == chapter.title:
        return latest
    version = ChapterVersion(
        novel_id=chapter.novel_id,
        chapter_no=chapter.chapter_no,
        chapter_id=chapter.id or 0,
        title=chapter.title or "",
        summary=chapter.summary,
        content=chapter.content,
        word_count=chapter.word_count or 0,
        source=source,
        created_at=now(),
    )
    db.add(version)
    db.flush()
    old_ids = [
        v.id for v in db.query(ChapterVersion.id)
        .filter(ChapterVersion.novel_id == chapter.novel_id, ChapterVersion.chapter_no == chapter.chapter_no)
        .order_by(ChapterVersion.id.desc())
        .offset(MAX_VERSIONS_PER_CHAPTER)
    ]
    if old_ids:
        db.query(ChapterVersion).filter(ChapterVersion.id.in_(old_ids)).delete(synchronize_session=False)
    return version


def delete_versions(db: Session, novel_id: int) -> None:
    db.query(ChapterVersion).filter(ChapterVersion.novel_id == novel_id).delete(synchronize_session=False)
