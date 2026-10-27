"""章节历史版本：查看 / 恢复；已删除章节的恢复"""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import dt_str, fail, now, ok
from app.models import Chapter, ChapterVersion, Novel
from app.routers.admin import _recount_novel, f_chapter_full
from app.services import chapter_versions, task_service

router = APIRouter(prefix="/api/admin", tags=["chapter-versions"])


def f_version(v: ChapterVersion, with_content: bool = False) -> dict:
    data = {
        "id": v.id, "novel_id": v.novel_id, "chapter_no": v.chapter_no,
        "title": v.title, "word_count": v.word_count,
        "source": v.source, "source_text": v.source_text,
        "preview": (v.content or "").strip().replace("\n", " ")[:80],
        "created_at": dt_str(v.created_at),
    }
    if with_content:
        data["content"] = v.content or ""
        data["summary"] = v.summary or ""
    return data


@router.get("/chapters/{cid}/versions")
def chapter_versions_index(cid: int, db: Session = Depends(get_db)):
    c = db.query(Chapter).filter(Chapter.id == cid).first()
    if not c:
        return fail("章节不存在")
    rows = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.novel_id == c.novel_id, ChapterVersion.chapter_no == c.chapter_no)
        .order_by(ChapterVersion.id.desc())
        .all()
    )
    return ok([f_version(v) for v in rows])


@router.get("/chapter-versions/{vid}")
def chapter_version_show(vid: int, db: Session = Depends(get_db)):
    v = db.query(ChapterVersion).filter(ChapterVersion.id == vid).first()
    if not v:
        return fail("版本不存在")
    return ok(f_version(v, with_content=True))


@router.get("/novels/{nid}/deleted-chapters")
def deleted_chapters(nid: int, db: Session = Depends(get_db)):
    """已删除章节：有历史版本、但当前没有这个章号的章节（每个章号取最新一个版本）"""
    existing = {no for (no,) in db.query(Chapter.chapter_no).filter(Chapter.novel_id == nid)}
    latest_ids = (
        db.query(func.max(ChapterVersion.id))
        .filter(ChapterVersion.novel_id == nid)
        .group_by(ChapterVersion.chapter_no)
    )
    rows = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.id.in_(latest_ids.scalar_subquery()))
        .order_by(ChapterVersion.chapter_no)
        .all()
    )
    return ok([f_version(v) for v in rows if v.chapter_no not in existing])


@router.post("/chapter-versions/{vid}/restore")
def chapter_version_restore(vid: int, db: Session = Depends(get_db)):
    v = db.query(ChapterVersion).filter(ChapterVersion.id == vid).first()
    if not v:
        return fail("版本不存在")
    novel = db.query(Novel).filter(Novel.id == v.novel_id).first()
    if not novel:
        return fail("小说不存在")
    try:
        with task_service.novel_guard(db, novel.id, "恢复版本"):
            chapter = (
                db.query(Chapter)
                .filter(Chapter.novel_id == v.novel_id, Chapter.chapter_no == v.chapter_no)
                .first()
            )
            if chapter:
                chapter_versions.save_version(db, chapter, "restore")  # 恢复也能撤销
            else:
                chapter = Chapter(novel_id=v.novel_id, chapter_no=v.chapter_no, status="published", created_at=now())
                db.add(chapter)
            chapter.title = v.title or f"第{v.chapter_no}章"
            chapter.content = v.content
            chapter.summary = v.summary
            chapter.word_count = v.word_count
            chapter.updated_at = now()
            db.commit()
            _recount_novel(db, novel)
    except RuntimeError as e:
        return fail(str(e))
    return ok(f_chapter_full(chapter), f"已恢复第{v.chapter_no}章到 {dt_str(v.created_at)} 的版本")
