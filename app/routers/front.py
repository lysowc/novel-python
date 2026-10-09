"""前台 API（/api/*，无需登录）——与 PHP 版契约一致"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import dt_str, fail, ok
from app.models import Chapter, Category, Novel, SystemConfig

router = APIRouter(prefix="/api", tags=["front"])

PUBLIC_STATUSES = ("published", "finished")


def _f_novel(n: Novel, with_created: bool = False) -> dict:
    data = {
        "id": n.id, "category_id": n.category_id,
        "category_name": n.category.name if n.category else "",
        "title": n.title, "cover": n.cover, "description": n.description,
        "tags": n.tags, "status": n.status, "status_text": n.status_text,
        "word_count": n.word_count, "chapter_count": n.chapter_count,
        "updated_at": dt_str(n.updated_at),
    }
    if with_created:
        data["created_at"] = dt_str(n.created_at)
    return data


def _category_list(db: Session) -> list[dict]:
    result = []
    for c in db.query(Category).filter(Category.status == 1).order_by(Category.sort, Category.id).all():
        count = (
            db.query(Novel)
            .filter(Novel.category_id == c.id, Novel.is_public == 1,
                    Novel.status.in_(PUBLIC_STATUSES))
            .count()
        )
        if count == 0:
            continue
        result.append({"id": c.id, "name": c.name, "novel_count": count})
    return result


@router.get("/home")
def home(db: Session = Depends(get_db)):
    site_name_row = db.query(SystemConfig).filter(SystemConfig.key == "site_name").first()
    recent = (
        db.query(Novel)
        .filter(Novel.is_public == 1, Novel.status.in_(PUBLIC_STATUSES))
        .order_by(Novel.updated_at.desc())
        .limit(12)
        .all()
    )
    return ok({
        "site_name": site_name_row.value if site_name_row else "AI 小说工坊",
        "recent_updates": [_f_novel(n) for n in recent],
        "categories": _category_list(db),
    })


@router.get("/categories")
def categories(db: Session = Depends(get_db)):
    return ok({"list": _category_list(db)})


@router.get("/novels")
def novels(page: int = Query(1), page_size: int = Query(12),
           category_id: int = Query(0), keyword: str = Query(""),
           db: Session = Depends(get_db)):
    page = max(1, page)
    page_size = min(60, max(1, page_size))
    q = db.query(Novel).filter(Novel.is_public == 1, Novel.status.in_(PUBLIC_STATUSES))
    if category_id > 0:
        q = q.filter(Novel.category_id == category_id)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(Novel.title.like(like) | Novel.description.like(like))
    total = q.count()
    rows = q.order_by(Novel.updated_at.desc(), Novel.id.desc()) \
        .offset((page - 1) * page_size).limit(page_size).all()
    return ok({"list": [_f_novel(n) for n in rows], "total": total, "page": page, "page_size": page_size})


@router.get("/novels/{nid}")
def novel_show(nid: int, db: Session = Depends(get_db)):
    n = (
        db.query(Novel)
        .filter(Novel.id == nid, Novel.is_public == 1, Novel.status.in_(PUBLIC_STATUSES))
        .first()
    )
    if not n:
        return fail("小说不存在或未公开", code=404)
    first_no = (
        db.query(Chapter.chapter_no)
        .filter(Chapter.novel_id == nid)
        .order_by(Chapter.chapter_no)
        .first()
    )
    data = _f_novel(n, with_created=True)
    data["first_no"] = first_no[0] if first_no else None
    return ok(data)


@router.get("/novels/{nid}/chapters")
def novel_chapters(nid: int, db: Session = Depends(get_db)):
    n = (
        db.query(Novel)
        .filter(Novel.id == nid, Novel.is_public == 1, Novel.status.in_(PUBLIC_STATUSES))
        .first()
    )
    if not n:
        return fail("小说不存在或未公开", code=404)
    rows = (
        db.query(Chapter)
        .filter(Chapter.novel_id == nid, Chapter.status == "published")
        .order_by(Chapter.chapter_no)
        .all()
    )
    return ok({
        "list": [{
            "chapter_no": c.chapter_no, "title": c.title,
            "word_count": c.word_count, "updated_at": dt_str(c.updated_at),
        } for c in rows],
        "total": len(rows),
    })


@router.get("/novels/{nid}/chapters/{no}")
def novel_read(nid: int, no: int, db: Session = Depends(get_db)):
    n = (
        db.query(Novel)
        .filter(Novel.id == nid, Novel.is_public == 1, Novel.status.in_(PUBLIC_STATUSES))
        .first()
    )
    if not n:
        return fail("小说不存在或未公开", code=404)
    chapter = (
        db.query(Chapter)
        .filter(Chapter.novel_id == nid, Chapter.chapter_no == no, Chapter.status == "published")
        .first()
    )
    if not chapter:
        return fail("章节不存在", code=404)
    prev_no = (
        db.query(Chapter.chapter_no)
        .filter(Chapter.novel_id == nid, Chapter.status == "published", Chapter.chapter_no < no)
        .order_by(Chapter.chapter_no.desc())
        .first()
    )
    next_no = (
        db.query(Chapter.chapter_no)
        .filter(Chapter.novel_id == nid, Chapter.status == "published", Chapter.chapter_no > no)
        .order_by(Chapter.chapter_no)
        .first()
    )
    return ok({
        "chapter": {
            "id": chapter.id, "chapter_no": chapter.chapter_no, "title": chapter.title,
            "content": chapter.content, "word_count": chapter.word_count,
            "updated_at": dt_str(chapter.updated_at),
        },
        "novel": {"id": n.id, "title": n.title},
        "prev_no": prev_no[0] if prev_no else None,
        "next_no": next_no[0] if next_no else None,
    })
