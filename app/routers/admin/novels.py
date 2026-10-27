"""后台 API：小说、章节、设定、记忆、大纲、审校"""
import json

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import dt_str, fail, now, ok, word_count
from app.models import (
    AiTask, Chapter, ConsistencyReport,
    Idea, Novel, NovelMemory, NovelSetting, CHAPTER_WRITTEN_STATUSES,
)
from app.services import chapter_versions, memory_store, task_service
from app.routers.admin.formatters import f_novel, f_chapter, f_chapter_full, f_task

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============ 小说 ============

@router.get("/novels")
def novels(page: int = Query(1), page_size: int = Query(10),
           keyword: str = Query(""), category_id: int = Query(0),
           status: str = Query(""), db: Session = Depends(get_db)):
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    q = db.query(Novel)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(Novel.title.like(like) | Novel.description.like(like))
    if category_id > 0:
        q = q.filter(Novel.category_id == category_id)
    if status in ("draft", "published", "finished"):
        q = q.filter(Novel.status == status)
    total = q.count()
    rows = q.order_by(Novel.updated_at.desc(), Novel.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return ok({"list": [f_novel(n) for n in rows], "total": total, "page": page, "page_size": page_size})


@router.post("/novels")
def novel_store(payload: dict, db: Session = Depends(get_db)):
    title = str(payload.get("title") or "").strip()
    if not title:
        return fail("书名不能为空")
    status = str(payload.get("status") or "draft")
    n = Novel(
        title=title, category_id=int(payload.get("category_id") or 0) or None,
        cover=str(payload.get("cover") or "").strip(),
        description=str(payload.get("description") or "").strip(),
        tags=str(payload.get("tags") or "").strip(),
        status=status if status in ("draft", "published", "finished") else "draft",
        is_public=1 if payload.get("is_public") else 0,
        word_count=0, chapter_count=0, created_at=now(), updated_at=now(),
    )
    db.add(n)
    db.commit()
    return ok(f_novel(n), "创建成功")


@router.get("/novels/{nid}")
def novel_show(nid: int, db: Session = Depends(get_db)):
    n = db.query(Novel).filter(Novel.id == nid).first()
    if not n:
        return fail("小说不存在")
    return ok(f_novel(n))


@router.put("/novels/{nid}")
def novel_update(nid: int, payload: dict, db: Session = Depends(get_db)):
    n = db.query(Novel).filter(Novel.id == nid).first()
    if not n:
        return fail("小说不存在")
    if payload.get("title") is not None:
        title = str(payload.get("title")).strip()
        if not title:
            return fail("书名不能为空")
        n.title = title
    if payload.get("category_id") is not None:
        n.category_id = int(payload.get("category_id")) or None
    if payload.get("cover") is not None:
        n.cover = str(payload.get("cover") or "").strip()
    if payload.get("description") is not None:
        n.description = str(payload.get("description") or "").strip()
    if payload.get("tags") is not None:
        n.tags = str(payload.get("tags") or "").strip()
    if payload.get("status") is not None and str(payload.get("status")) in ("draft", "published", "finished"):
        n.status = str(payload.get("status"))
    if payload.get("is_public") is not None:
        n.is_public = 1 if payload.get("is_public") else 0
    n.updated_at = now()
    db.commit()
    return ok(f_novel(n), "已保存")


@router.delete("/novels/{nid}")
def novel_destroy(nid: int, db: Session = Depends(get_db)):
    n = db.query(Novel).filter(Novel.id == nid).first()
    if not n:
        return fail("小说不存在")
    try:
        with task_service.novel_guard(db, nid, "删除"):
            db.query(Chapter).filter(Chapter.novel_id == nid).delete()
            db.query(NovelSetting).filter(NovelSetting.novel_id == nid).delete()
            db.query(NovelMemory).filter(NovelMemory.novel_id == nid).delete()
            db.query(ConsistencyReport).filter(ConsistencyReport.novel_id == nid).delete()
            memory_store.delete_snapshots(db, nid)
            chapter_versions.delete_versions(db, nid)
            db.query(AiTask).filter(AiTask.ref_id == nid, AiTask.ref_type == "novel").delete(synchronize_session=False)
            db.query(Idea).filter(Idea.novel_id == nid).update({"novel_id": None}, synchronize_session=False)
            db.delete(n)
            db.commit()
    except RuntimeError as e:
        return fail(str(e))
    return ok(None, "已删除")


@router.get("/novels/{nid}/chapters")
def novel_chapters(nid: int, db: Session = Depends(get_db)):
    if not db.query(Novel).filter(Novel.id == nid).first():
        return fail("小说不存在")
    rows = db.query(Chapter).filter(Chapter.novel_id == nid).order_by(Chapter.chapter_no).all()
    return ok([f_chapter(c) for c in rows])


@router.post("/novels/{nid}/chapters/publish")
def novel_chapters_publish(nid: int, payload: dict | None = None, db: Session = Depends(get_db)):
    """批量发布草稿章节：body {"ids": [...]}；不传 ids 则发布全部草稿"""
    if not db.query(Novel).filter(Novel.id == nid).first():
        return fail("小说不存在")
    q = db.query(Chapter).filter(Chapter.novel_id == nid, Chapter.status == "draft")
    ids = (payload or {}).get("ids")
    if isinstance(ids, list) and ids:
        q = q.filter(Chapter.id.in_([int(i) for i in ids]))
    count = q.update({"status": "published", "updated_at": now()}, synchronize_session=False)
    db.commit()
    return ok({"count": count}, f"已发布 {count} 章" if count else "没有需要发布的草稿")


@router.post("/novels/{nid}/chapters")
def novel_chapter_store(nid: int, payload: dict, db: Session = Depends(get_db)):
    n = db.query(Novel).filter(Novel.id == nid).first()
    if not n:
        return fail("小说不存在")
    title = str(payload.get("title") or "").strip()
    if not title:
        return fail("章节标题不能为空")
    from sqlalchemy import func
    chapter_no = int(db.query(func.max(Chapter.chapter_no)).filter(Chapter.novel_id == nid).scalar() or 0) + 1
    content = str(payload.get("content") or "")
    c = Chapter(
        novel_id=nid, chapter_no=chapter_no, title=title,
        summary=str(payload.get("summary") or "").strip(), content=content,
        word_count=word_count(content), status="published", created_at=now(), updated_at=now(),
    )
    db.add(c)
    db.commit()
    _recount_novel(db, n)
    return ok(f_chapter(c), "章节已添加")


@router.get("/novels/{nid}/setting")
@router.put("/novels/{nid}/setting")
def novel_setting(nid: int, payload: dict | None = None, request: Request = None,
                  db: Session = Depends(get_db)):
    if not db.query(Novel).filter(Novel.id == nid).first():
        return fail("小说不存在")
    setting = db.query(NovelSetting).filter(NovelSetting.novel_id == nid).first()
    if request.method == "PUT":
        payload = payload or {}
        if not setting:
            setting = NovelSetting(novel_id=nid, created_at=now())
            db.add(setting)
        for field in ("world_view", "characters", "factions", "conflicts", "main_plot", "style"):
            if payload.get(field) is not None:
                setattr(setting, field, str(payload.get(field)))
        setting.updated_at = now()
        db.commit()
        return ok({
            "id": setting.id, "novel_id": setting.novel_id,
            "world_view": setting.world_view, "characters": setting.characters,
            "factions": setting.factions, "conflicts": setting.conflicts,
            "main_plot": setting.main_plot, "style": setting.style,
            "created_at": dt_str(setting.created_at), "updated_at": dt_str(setting.updated_at),
        }, "设定已保存")
    if setting:
        return ok({
            "id": setting.id, "novel_id": setting.novel_id,
            "world_view": setting.world_view, "characters": setting.characters,
            "factions": setting.factions, "conflicts": setting.conflicts,
            "main_plot": setting.main_plot, "style": setting.style,
            "created_at": dt_str(setting.created_at), "updated_at": dt_str(setting.updated_at),
        })
    return ok({"novel_id": nid, "world_view": "", "characters": "", "factions": "",
               "conflicts": "", "main_plot": "", "style": ""})


@router.get("/novels/{nid}/memory")
@router.put("/novels/{nid}/memory")
def novel_memory(nid: int, payload: dict | None = None, request: Request = None,
                 db: Session = Depends(get_db)):
    if not db.query(Novel).filter(Novel.id == nid).first():
        return fail("小说不存在")
    memory = db.query(NovelMemory).filter(NovelMemory.novel_id == nid).first()
    if request.method == "PUT":
        content = str((payload or {}).get("content") or "")
        if content.strip():
            try:
                data = json.loads(content)
            except ValueError:
                return fail("记忆必须是合法 JSON")
            if not isinstance(data, (dict, list)):
                return fail("记忆必须是合法 JSON")
        if not memory:
            memory = NovelMemory(novel_id=nid)
            db.add(memory)
        memory.content = content
        memory.updated_at = now()
        # 手动修改的记忆同步为最后一章的快照，之后重写下一章时也能用上
        last_no = memory_store.max_chapter_no(db, nid)
        if last_no > 0:
            memory_store.save_snapshot(db, nid, last_no, content)
        db.commit()
        return ok({"content": content, "updated_at": dt_str(memory.updated_at)}, "记忆已保存")
    return ok({"content": memory.content if memory else "", "updated_at": dt_str(memory.updated_at if memory else None)})


@router.get("/novels/{nid}/outline")
@router.put("/novels/{nid}/outline")
def novel_outline(nid: int, payload: dict | None = None, request: Request = None,
                  db: Session = Depends(get_db)):
    n = db.query(Novel).filter(Novel.id == nid).first()
    if not n:
        return fail("小说不存在")
    if request.method == "PUT":
        outline = str((payload or {}).get("outline") or "")
        if outline.strip():
            try:
                data = json.loads(outline)
            except ValueError:
                return fail("大纲 JSON 格式错误（需要 {\"volumes\":[...]}）")
            if not isinstance(data, dict) or "volumes" not in data:
                return fail("大纲 JSON 格式错误（需要 {\"volumes\":[...]}）")
        n.outline = outline
        n.updated_at = now()
        db.commit()
        return ok({"outline": outline}, "大纲已保存")
    return ok({"outline": n.outline or ""})


@router.get("/novels/{nid}/consistency")
def consistency_index(nid: int, db: Session = Depends(get_db)):
    if not db.query(Novel).filter(Novel.id == nid).first():
        return fail("小说不存在")
    rows = db.query(ConsistencyReport).filter(ConsistencyReport.novel_id == nid) \
        .order_by(ConsistencyReport.id.desc()).limit(50).all()
    return ok([{
        "id": r.id, "novel_id": r.novel_id, "chapter_no": r.chapter_no,
        "status": r.status, "report": r.report, "created_at": dt_str(r.created_at),
    } for r in rows])


@router.post("/novels/{nid}/consistency")
def consistency_run(nid: int, db: Session = Depends(get_db)):
    if not db.query(Novel).filter(Novel.id == nid).first():
        return fail("小说不存在")
    try:
        task = task_service.create(db, "consistency_check", nid, {})
    except RuntimeError as e:
        return fail(str(e))
    return ok(f_task(task), "审校任务已创建")


def _recount_novel(db: Session, n: Novel) -> None:
    from sqlalchemy import func
    n.word_count = int(db.query(func.sum(Chapter.word_count)).filter(Chapter.novel_id == n.id).scalar() or 0)
    n.chapter_count = db.query(Chapter).filter(Chapter.novel_id == n.id).count()
    db.commit()


# ============ 章节 ============

@router.get("/chapters/{cid}")
def chapter_show(cid: int, db: Session = Depends(get_db)):
    c = db.query(Chapter).filter(Chapter.id == cid).first()
    if not c:
        return fail("章节不存在")
    return ok(f_chapter_full(c))


@router.put("/chapters/{cid}")
def chapter_update(cid: int, payload: dict, db: Session = Depends(get_db)):
    c = db.query(Chapter).filter(Chapter.id == cid).first()
    if not c:
        return fail("章节不存在")
    if payload.get("title") is not None:
        title = str(payload.get("title")).strip()
        if not title:
            return fail("章节标题不能为空")
        c.title = title
    if payload.get("summary") is not None:
        c.summary = str(payload.get("summary"))
    if payload.get("status") is not None:
        if str(payload.get("status")) not in CHAPTER_WRITTEN_STATUSES:
            return fail("章节状态只能是 draft 或 published")
        c.status = str(payload.get("status"))
    content_changed = payload.get("content") is not None and str(payload.get("content")) != (c.content or "")
    if content_changed:
        chapter_versions.save_version(db, c, "edit")
        c.content = str(payload.get("content"))
        c.word_count = word_count(c.content)
    c.updated_at = now()
    db.commit()
    if content_changed:
        n = db.query(Novel).filter(Novel.id == c.novel_id).first()
        if n:
            _recount_novel(db, n)
    return ok(f_chapter(c), "已保存")


@router.delete("/chapters/{cid}")
def chapter_destroy(cid: int, db: Session = Depends(get_db)):
    c = db.query(Chapter).filter(Chapter.id == cid).first()
    if not c:
        return fail("章节不存在")
    novel_id = c.novel_id
    chapter_versions.save_version(db, c, "delete")  # 可在「已删除章节」里恢复
    memory_store.delete_snapshots(db, novel_id, c.chapter_no)
    db.delete(c)
    db.commit()
    n = db.query(Novel).filter(Novel.id == novel_id).first()
    if n:
        _recount_novel(db, n)
    return ok(None, "已删除")
