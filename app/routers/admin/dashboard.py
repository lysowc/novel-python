"""后台 API：仪表盘与分类"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import fail, now, ok
from app.models import Admin, AiLog, AiTask, Category, Chapter, Idea, Novel
from app.services import security
from app.routers.admin.formatters import f_category, f_task, f_log

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============ 仪表盘 ============

@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    from datetime import datetime
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    recent_tasks = [f_task(t) for t in db.query(AiTask).order_by(AiTask.id.desc()).limit(10)]
    recent_logs = [f_log(l) for l in db.query(AiLog).order_by(AiLog.id.desc()).limit(10)]
    admin = db.query(Admin).filter(Admin.id == request.session.get("admin_id")).first()
    return ok({
        "novel_count": db.query(Novel).count(),
        "chapter_count": db.query(Chapter).count(),
        "total_words": _sum_word_count(db),
        "idea_count": db.query(Idea).count(),
        "running_tasks": db.query(AiTask).filter(AiTask.status.in_(["pending", "running"])).count(),
        "today_chapters": db.query(Chapter).filter(Chapter.created_at >= today).count(),
        "recent_tasks": recent_tasks,
        "recent_logs": recent_logs,
        "security_warnings": security.config_warnings(admin.password if admin else None),
    })


def _sum_word_count(db: Session) -> int:
    from sqlalchemy import func
    return int(db.query(func.sum(Novel.word_count)).scalar() or 0)


# ============ 分类 ============

@router.get("/categories")
def categories(db: Session = Depends(get_db)):
    rows = db.query(Category).order_by(Category.sort, Category.id).all()
    from sqlalchemy import func
    counts = dict(
        db.query(Novel.category_id, func.count(Novel.id))
        .group_by(Novel.category_id).all()
    )
    return ok({"list": [f_category(c, counts.get(c.id, 0)) for c in rows]})


@router.post("/categories")
def category_store(payload: dict, db: Session = Depends(get_db)):
    name = str(payload.get("name") or "").strip()
    if not name:
        return fail("分类名不能为空")
    if db.query(Category).filter(Category.name == name).first():
        return fail("分类名已存在")
    c = Category(
        name=name, description=str(payload.get("description") or "").strip(),
        sort=int(payload.get("sort") or 0), status=1 if payload.get("status", 1) else 0,
        created_at=now(), updated_at=now(),
    )
    db.add(c)
    db.commit()
    return ok(f_category(c), "创建成功")


@router.put("/categories/{cid}")
def category_update(cid: int, payload: dict, db: Session = Depends(get_db)):
    c = db.query(Category).filter(Category.id == cid).first()
    if not c:
        return fail("分类不存在")
    name = str(payload.get("name") or "").strip()
    if not name:
        return fail("分类名不能为空")
    if db.query(Category).filter(Category.name == name, Category.id != cid).first():
        return fail("分类名已存在")
    c.name = name
    c.description = str(payload.get("description") or "").strip()
    c.sort = int(payload.get("sort", c.sort))
    c.status = 1 if payload.get("status", c.status) else 0
    c.updated_at = now()
    db.commit()
    return ok(f_category(c), "已保存")


@router.delete("/categories/{cid}")
def category_destroy(cid: int, db: Session = Depends(get_db)):
    c = db.query(Category).filter(Category.id == cid).first()
    if not c:
        return fail("分类不存在")
    if db.query(Novel).filter(Novel.category_id == cid).first():
        return fail("该分类下还有小说，无法删除")
    db.delete(c)
    db.commit()
    return ok(None, "已删除")
