"""后台 API（/api/admin/*，除 login 外均需登录）——与 PHP 版契约逐一对应"""
import json
import re

import bcrypt
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.helpers import dt_str, fail, fail_401, json_dumps, now, ok, word_count
from app.models import (
    Admin, AiLog, AiModel, AiProvider, AiTask, Category, Chapter, ConsistencyReport,
    Idea, IdeaChat, Novel, NovelMemory, NovelSetting, Prompt, SystemConfig,
)
from app.services import prompt_service, task_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============ 格式化工（时间统一 Y-m-d H:i:s） ============

def f_category(c: Category, novel_count: int = 0) -> dict:
    return {
        "id": c.id, "name": c.name, "description": c.description, "sort": c.sort,
        "status": c.status, "novel_count": novel_count,
        "created_at": dt_str(c.created_at), "updated_at": dt_str(c.updated_at),
    }


def f_novel(n: Novel, with_public: bool = True) -> dict:
    data = {
        "id": n.id, "category_id": n.category_id,
        "category_name": n.category.name if n.category else "",
        "title": n.title, "cover": n.cover, "description": n.description,
        "tags": n.tags, "status": n.status, "status_text": n.status_text,
        "word_count": n.word_count, "chapter_count": n.chapter_count,
        "created_at": dt_str(n.created_at), "updated_at": dt_str(n.updated_at),
    }
    if with_public:
        data["is_public"] = n.is_public
    return data


def f_chapter(c: Chapter) -> dict:
    return {
        "id": c.id, "novel_id": c.novel_id, "chapter_no": c.chapter_no,
        "title": c.title, "summary": c.summary, "word_count": c.word_count,
        "status": c.status, "created_at": dt_str(c.created_at), "updated_at": dt_str(c.updated_at),
    }


def f_idea(i: Idea) -> dict:
    return {
        "id": i.id, "category_id": i.category_id,
        "category_name": i.category.name if i.category else "",
        "title": i.title, "content": i.content, "status": i.status,
        "novel_id": i.novel_id, "created_at": dt_str(i.created_at), "updated_at": dt_str(i.updated_at),
    }


def f_provider(p: AiProvider) -> dict:
    return {
        "id": p.id, "name": p.name, "base_url": p.base_url, "api_key": p.masked_key,
        "status": p.status, "is_default": p.is_default,
        "created_at": dt_str(p.created_at), "updated_at": dt_str(p.updated_at),
    }


def f_model(m: AiModel) -> dict:
    return {
        "id": m.id, "provider_id": m.provider_id,
        "provider_name": m.provider.name if m.provider else "",
        "name": m.name, "display_name": m.display_name,
        "max_tokens": m.max_tokens,
        "temperature": float(m.temperature) if m.temperature is not None else None,
        "status": m.status, "is_default": m.is_default,
        "created_at": dt_str(m.created_at), "updated_at": dt_str(m.updated_at),
    }


def f_task(t: AiTask) -> dict:
    return {
        "id": t.id, "task_type": t.task_type, "task_type_text": t.type_text,
        "ref_id": t.ref_id, "ref_type": t.ref_type, "params": t.params,
        "status": t.status, "error_message": t.error_message,
        "created_at": dt_str(t.created_at), "updated_at": dt_str(t.updated_at),
    }


def f_log(l: AiLog) -> dict:
    return {
        "id": l.id, "provider": l.provider, "model": l.model, "task_type": l.task_type,
        "prompt_tokens": l.prompt_tokens, "completion_tokens": l.completion_tokens,
        "total_tokens": l.total_tokens, "duration": l.duration, "status": l.status,
        "error_message": l.error_message, "created_at": dt_str(l.created_at),
    }


# ============ 登录（无需鉴权） ============

login_router = APIRouter(prefix="/api/admin", tags=["admin-auth"])


@login_router.post("/login")
def login(payload: dict, request: Request, db: Session = Depends(get_db)):
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if not username or not password:
        return fail("请输入账号和密码")
    admin = db.query(Admin).filter(Admin.username == username).first()
    if not admin or not _verify_password(password, admin.password):
        return fail("账号或密码错误")
    request.session["admin_id"] = admin.id
    admin.last_login_at = now()
    db.commit()
    return ok({"username": admin.username}, "登录成功")


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


# ============ 鉴权信息 ============

@router.post("/logout")
def logout(request: Request):
    request.session.pop("admin_id", None)
    return ok(None, "已退出登录")


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    admin = db.query(Admin).filter(Admin.id == request.session.get("admin_id")).first()
    return ok({"username": admin.username if admin else ""})


@router.put("/password")
def change_password(payload: dict, request: Request, db: Session = Depends(get_db)):
    old = str(payload.get("old_password") or "")
    new = str(payload.get("new_password") or "")
    if len(new) < 6:
        return fail("新密码至少 6 位")
    admin = db.query(Admin).filter(Admin.id == request.session.get("admin_id")).first()
    if not admin or not _verify_password(old, admin.password):
        return fail("原密码错误")
    admin.password = bcrypt.hashpw(new.encode(), bcrypt.gensalt()).decode()
    db.commit()
    return ok(None, "密码已修改")


# ============ 仪表盘 ============

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    from datetime import datetime
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    recent_tasks = [f_task(t) for t in db.query(AiTask).order_by(AiTask.id.desc()).limit(10)]
    recent_logs = [f_log(l) for l in db.query(AiLog).order_by(AiLog.id.desc()).limit(10)]
    return ok({
        "novel_count": db.query(Novel).count(),
        "chapter_count": db.query(Chapter).count(),
        "total_words": _sum_word_count(db),
        "idea_count": db.query(Idea).count(),
        "running_tasks": db.query(AiTask).filter(AiTask.status.in_(["pending", "running"])).count(),
        "today_chapters": db.query(Chapter).filter(Chapter.created_at >= today).count(),
        "recent_tasks": recent_tasks,
        "recent_logs": recent_logs,
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
    db.query(Chapter).filter(Chapter.novel_id == nid).delete()
    db.query(NovelSetting).filter(NovelSetting.novel_id == nid).delete()
    db.query(NovelMemory).filter(NovelMemory.novel_id == nid).delete()
    db.query(ConsistencyReport).filter(ConsistencyReport.novel_id == nid).delete()
    db.delete(n)
    db.commit()
    return ok(None, "已删除")


@router.get("/novels/{nid}/chapters")
def novel_chapters(nid: int, db: Session = Depends(get_db)):
    if not db.query(Novel).filter(Novel.id == nid).first():
        return fail("小说不存在")
    rows = db.query(Chapter).filter(Chapter.novel_id == nid).order_by(Chapter.chapter_no).all()
    return ok([f_chapter(c) for c in rows])


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
    content_changed = payload.get("content") is not None
    if content_changed:
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
    db.delete(c)
    db.commit()
    n = db.query(Novel).filter(Novel.id == novel_id).first()
    if n:
        _recount_novel(db, n)
    return ok(None, "已删除")


# ============ 点子 ============

@router.get("/ideas")
def ideas(page: int = Query(1), page_size: int = Query(10),
          status: str = Query(""), category_id: int = Query(0),
          db: Session = Depends(get_db)):
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    q = db.query(Idea)
    if status in ("unused", "used"):
        q = q.filter(Idea.status == status)
    if category_id > 0:
        q = q.filter(Idea.category_id == category_id)
    total = q.count()
    rows = q.order_by(Idea.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return ok({"list": [f_idea(i) for i in rows], "total": total, "page": page, "page_size": page_size})


@router.post("/ideas")
def idea_store(payload: dict, db: Session = Depends(get_db)):
    title = str(payload.get("title") or "").strip()
    if not title:
        return fail("点子标题不能为空")
    i = Idea(
        category_id=int(payload.get("category_id") or 0) or None, title=title,
        content=str(payload.get("content") or "").strip(), status="unused",
        created_at=now(), updated_at=now(),
    )
    db.add(i)
    db.commit()
    return ok(f_idea(i), "创建成功")


@router.put("/ideas/{iid}")
def idea_update(iid: int, payload: dict, db: Session = Depends(get_db)):
    i = db.query(Idea).filter(Idea.id == iid).first()
    if not i:
        return fail("点子不存在")
    if payload.get("title") is not None:
        i.title = str(payload.get("title")).strip() or i.title
    if payload.get("content") is not None:
        i.content = str(payload.get("content"))
    if payload.get("category_id") is not None:
        i.category_id = int(payload.get("category_id")) or None
    i.updated_at = now()
    db.commit()
    return ok(f_idea(i), "已保存")


@router.delete("/ideas/{iid}")
def idea_destroy(iid: int, db: Session = Depends(get_db)):
    i = db.query(Idea).filter(Idea.id == iid).first()
    if not i:
        return fail("点子不存在")
    db.query(IdeaChat).filter(IdeaChat.idea_id == iid).delete()
    db.delete(i)
    db.commit()
    return ok(None, "已删除")


@router.get("/ideas/{iid}/messages")
def idea_messages(iid: int, db: Session = Depends(get_db)):
    if not db.query(Idea).filter(Idea.id == iid).first():
        return fail("点子不存在")
    rows = db.query(IdeaChat).filter(IdeaChat.idea_id == iid).order_by(IdeaChat.id).all()
    return ok([{
        "id": c.id, "role": c.role, "content": c.content, "created_at": dt_str(c.created_at),
    } for c in rows])


@router.post("/ideas/{iid}/chat")
def idea_chat(iid: int, payload: dict, db: Session = Depends(get_db)):
    """点子聊天（SSE 流式，同步直连 AI）"""
    idea = db.query(Idea).filter(Idea.id == iid).first()
    if not idea:
        return fail("点子不存在")
    message = str(payload.get("message") or "").strip()
    if not message:
        return fail("消息不能为空")

    db.add(IdeaChat(idea_id=iid, role="user", content=message, created_at=now()))
    db.commit()
    db.close()  # 流式生成器使用独立会话

    def generate_stream():
        import queue
        import threading

        from app.services.ai_client import AiClient

        stream_db = _open_db()
        try:
            idea_row = stream_db.query(Idea).filter(Idea.id == iid).first()
            history_row = stream_db.query(IdeaChat).filter(IdeaChat.idea_id == iid).order_by(IdeaChat.id).all()
            all_msgs = [{"role": c.role, "content": c.content} for c in history_row]

            # 上下文压缩：≤22 条全带；否则最早 2 条 + 最近 20 条；再按 3 万字从尾部截断
            if len(all_msgs) <= 22:
                msgs = list(all_msgs)
            else:
                msgs = all_msgs[:2] + all_msgs[-20:]
            total = 0
            trimmed = []
            for m in reversed(msgs):
                if total + len(m["content"]) > 30000 and trimmed:
                    break
                trimmed.append(m)
                total += len(m["content"])
            msgs = list(reversed(trimmed))
            omitted = len(all_msgs) - len(msgs)
            if omitted > 0:
                msgs.insert(0, {
                    "role": "system",
                    "content": f"（注意：更早的 {omitted} 条讨论记录因长度限制已省略，请基于现有上下文继续讨论，必要时先向作者确认此前确定的关键设定。）",
                })

            category = idea_row.category.name if idea_row.category else ""
            background = "这是关于一部小说的点子讨论。"
            if category:
                background += f"题材方向：{category}。"
            if (idea_row.content or "").strip():
                background += f"\n当前已记录的点子草稿：{idea_row.content}"
            msgs.insert(0, {"role": "system", "content": prompt_service.render(stream_db, "idea_chat") + "\n\n" + background})

            # AI 调用放在独立线程，增量经队列桥接给 SSE 生成器
            q: queue.Queue = queue.Queue()

            def run_ai():
                try:
                    result = AiClient(stream_db).chat_stream(
                        msgs, {"task_type": "idea_chat"},
                        on_delta=lambda d: q.put(("delta", d)),
                    )
                    q.put(("result", result))
                except Exception as e:
                    q.put(("error", str(e)))

            thread = threading.Thread(target=run_ai, daemon=True)
            thread.start()

            full = ""
            error_msg = None
            while True:
                kind, payload = q.get()
                if kind == "delta":
                    full += payload
                    yield "data: " + json_dumps({"type": "delta", "content": payload}) + "\n\n"
                elif kind == "error":
                    error_msg = payload
                    break
                else:
                    break
            thread.join()

            # 出错时只发 error，不再发 done（避免前端把错误状态冲掉）
            if error_msg is not None:
                yield "data: " + json_dumps({"type": "error", "msg": error_msg}) + "\n\n"
                return

            full = full.strip()
            if full:
                stream_db.add(IdeaChat(idea_id=iid, role="assistant", content=full, created_at=now()))
                stream_db.commit()
            yield "data: " + json_dumps({"type": "done"}) + "\n\n"
        except Exception as e:
            yield "data: " + json_dumps({"type": "error", "msg": str(e)}) + "\n\n"
        finally:
            stream_db.close()

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _open_db():
    from app.db import SessionLocal
    return SessionLocal()


@router.post("/ideas/{iid}/save")
def idea_save(iid: int, db: Session = Depends(get_db)):
    """保存点子：AI 提炼标题与内容（失败时降级）"""
    from app.services.ai_client import AiClient

    idea = db.query(Idea).filter(Idea.id == iid).first()
    if not idea:
        return fail("点子不存在")
    if idea.status == "used":
        return fail("该点子已生成小说，不能修改")
    chats = db.query(IdeaChat).filter(IdeaChat.idea_id == iid).order_by(IdeaChat.id).all()
    if not chats:
        return fail("还没有聊天内容")
    transcript = "\n".join(("作者：" if c.role == "user" else "AI：") + c.content for c in chats)
    if len(transcript) > 15000:
        transcript = transcript[:3000] + "\n……（中间讨论省略）……\n" + transcript[-12000:]

    title, content = "", ""
    try:
        system = (
            "你是一位网文策划编辑。请根据下面的讨论记录，提炼出小说点子。\n"
            "严格只输出两行：\n"
            "第一行：标题（20 字以内）\n"
            "第二行：点子内容（200~400 字，包含核心创意、主角设定、世界观、卖点）"
        )
        result = AiClient(db).chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": f"讨论记录：\n{transcript}"}],
            {"task_type": "idea_save"},
        )
        lines = [l.strip() for l in result["text"].strip().split("\n") if l.strip()]
        title = lines[0] if lines else ""
        content = "\n".join(lines[1:])
        title = re.sub(r"^(标题|书名)[:：]\s*", "", title)
    except Exception:
        title, content = "", ""

    if not title or not content:
        last_user = next((c for c in reversed(chats) if c.role == "user"), None)
        fallback = last_user.content.strip() if last_user else ""
        if not title:
            title = fallback[:20] or "未命名点子"
        if not content:
            content = fallback or transcript

    idea.title = title
    idea.content = content
    idea.updated_at = now()
    db.commit()
    return ok(f_idea(idea), "点子已保存")


@router.post("/ideas/{iid}/create-novel")
def idea_create_novel(iid: int, db: Session = Depends(get_db)):
    idea = db.query(Idea).filter(Idea.id == iid).first()
    if not idea:
        return fail("点子不存在")
    if idea.status == "used" or idea.novel_id:
        return fail("该点子已生成过小说")
    n = Novel(
        category_id=idea.category_id, title=idea.title, cover="",
        description=idea.content or "", tags="", status="draft", is_public=0,
        word_count=0, chapter_count=0, created_at=now(), updated_at=now(),
    )
    db.add(n)
    db.commit()
    idea.novel_id = n.id
    idea.status = "used"
    idea.updated_at = now()
    db.commit()
    task = task_service.create(db, "generate_setting", n.id)
    return ok({"novel_id": n.id, "task_id": task.id}, "小说已创建，AI 正在生成设定")


# ============ AI 配置 ============

@router.get("/ai/providers")
def ai_providers(db: Session = Depends(get_db)):
    rows = db.query(AiProvider).order_by(AiProvider.is_default.desc(), AiProvider.id).all()
    return ok([f_provider(p) for p in rows])


@router.post("/ai/providers")
def ai_provider_store(payload: dict, db: Session = Depends(get_db)):
    name = str(payload.get("name") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()
    if not name or not base_url:
        return fail("名称和 base_url 不能为空")
    p = AiProvider(
        name=name, base_url=base_url, api_key=str(payload.get("api_key") or "").strip(),
        status=1 if payload.get("status", 1) else 0,
        is_default=1 if db.query(AiProvider).count() == 0 else 0,
        created_at=now(), updated_at=now(),
    )
    db.add(p)
    db.commit()
    return ok({"id": p.id, "api_key": p.masked_key}, "创建成功")


@router.put("/ai/providers/{pid}")
def ai_provider_update(pid: int, payload: dict, db: Session = Depends(get_db)):
    p = db.query(AiProvider).filter(AiProvider.id == pid).first()
    if not p:
        return fail("Provider 不存在")
    if payload.get("name") is not None:
        p.name = str(payload.get("name")).strip() or p.name
    if payload.get("base_url") is not None:
        p.base_url = str(payload.get("base_url")).strip() or p.base_url
    if payload.get("api_key") is not None and str(payload.get("api_key")).strip():
        p.api_key = str(payload.get("api_key")).strip()
    if payload.get("status") is not None:
        p.status = 1 if payload.get("status") else 0
    p.updated_at = now()
    db.commit()
    return ok({"id": p.id, "api_key": p.masked_key}, "已保存")


@router.delete("/ai/providers/{pid}")
def ai_provider_destroy(pid: int, db: Session = Depends(get_db)):
    p = db.query(AiProvider).filter(AiProvider.id == pid).first()
    if not p:
        return fail("Provider 不存在")
    if db.query(AiModel).filter(AiModel.provider_id == pid).first():
        return fail("该 Provider 下还有模型，请先删除模型")
    db.delete(p)
    db.commit()
    return ok(None, "已删除")


@router.post("/ai/providers/{pid}/default")
def ai_provider_default(pid: int, db: Session = Depends(get_db)):
    p = db.query(AiProvider).filter(AiProvider.id == pid).first()
    if not p:
        return fail("Provider 不存在")
    db.query(AiProvider).update({AiProvider.is_default: 0})
    p.is_default = 1
    db.commit()
    return ok(None, "已设为默认")


@router.get("/ai/models")
def ai_models(db: Session = Depends(get_db)):
    rows = db.query(AiModel).order_by(AiModel.is_default.desc(), AiModel.id).all()
    return ok([f_model(m) for m in rows])


@router.post("/ai/models")
def ai_model_store(payload: dict, db: Session = Depends(get_db)):
    provider_id = int(payload.get("provider_id") or 0)
    name = str(payload.get("name") or "").strip()
    if not provider_id or not db.query(AiProvider).filter(AiProvider.id == provider_id).first():
        return fail("请选择 Provider")
    if not name:
        return fail("模型名不能为空")
    m = AiModel(
        provider_id=provider_id, name=name,
        display_name=str(payload.get("display_name") or name).strip(),
        max_tokens=int(payload.get("max_tokens") or 0) or None,
        temperature=float(payload.get("temperature")) if payload.get("temperature") not in (None, "") else None,
        status=1 if payload.get("status", 1) else 0,
        is_default=1 if db.query(AiModel).count() == 0 else 0,
        created_at=now(), updated_at=now(),
    )
    db.add(m)
    db.commit()
    return ok(f_model(m), "创建成功")


@router.put("/ai/models/{mid}")
def ai_model_update(mid: int, payload: dict, db: Session = Depends(get_db)):
    m = db.query(AiModel).filter(AiModel.id == mid).first()
    if not m:
        return fail("模型不存在")
    if payload.get("provider_id") is not None:
        provider_id = int(payload.get("provider_id"))
        if not db.query(AiProvider).filter(AiProvider.id == provider_id).first():
            return fail("Provider 不存在")
        m.provider_id = provider_id
    if payload.get("name") is not None:
        m.name = str(payload.get("name")).strip() or m.name
    if payload.get("display_name") is not None:
        m.display_name = str(payload.get("display_name")).strip()
    if payload.get("max_tokens") is not None:
        m.max_tokens = int(payload.get("max_tokens")) or None
    if payload.get("temperature") is not None:
        m.temperature = None if payload.get("temperature") == "" else float(payload.get("temperature"))
    if payload.get("status") is not None:
        m.status = 1 if payload.get("status") else 0
    m.updated_at = now()
    db.commit()
    return ok(f_model(m), "已保存")


@router.delete("/ai/models/{mid}")
def ai_model_destroy(mid: int, db: Session = Depends(get_db)):
    m = db.query(AiModel).filter(AiModel.id == mid).first()
    if not m:
        return fail("模型不存在")
    db.delete(m)
    db.commit()
    return ok(None, "已删除")


@router.post("/ai/models/{mid}/default")
def ai_model_default(mid: int, db: Session = Depends(get_db)):
    m = db.query(AiModel).filter(AiModel.id == mid).first()
    if not m:
        return fail("模型不存在")
    db.query(AiModel).update({AiModel.is_default: 0})
    m.is_default = 1
    db.commit()
    return ok(None, "已设为默认")


# ============ Prompt ============

@router.get("/prompts")
def prompts(db: Session = Depends(get_db)):
    rows = db.query(Prompt).order_by(Prompt.id).all()
    return ok([{
        "id": p.id, "type": p.type, "name": p.name, "description": p.description,
        "content": p.content, "variables": prompt_service.VARIABLES.get(p.type, []),
        "updated_at": dt_str(p.updated_at),
    } for p in rows])


@router.put("/prompts/{pid}")
def prompt_update(pid: int, payload: dict, db: Session = Depends(get_db)):
    p = db.query(Prompt).filter(Prompt.id == pid).first()
    if not p:
        return fail("Prompt 不存在")
    content = str(payload.get("content") or "")
    if not content.strip():
        return fail("Prompt 内容不能为空")
    p.content = content
    p.updated_at = now()
    db.commit()
    return ok(None, "已保存")


# ============ AI 日志 ============

@router.get("/ai/logs")
def ai_logs(page: int = Query(1), page_size: int = Query(20), db: Session = Depends(get_db)):
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    total = db.query(AiLog).count()
    rows = db.query(AiLog).order_by(AiLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return ok({"list": [f_log(l) for l in rows], "total": total, "page": page, "page_size": page_size})


@router.delete("/ai/logs")
def ai_logs_clear(db: Session = Depends(get_db)):
    db.query(AiLog).delete()
    db.commit()
    return ok(None, "日志已清空")


# ============ 系统配置 ============

CONFIG_KEYS = {
    "site_name": "站点名称",
    "ai_temperature": "AI 默认温度",
    "ai_http_timeout": "AI HTTP 超时（秒）",
    "context_max_recent_chapters": "上下文最近章节数",
    "context_summary_max_chars": "上下文摘要字符上限",
    "chapter_target_words": "每章目标字数",
    "outline_volumes": "大纲默认卷数",
    "outline_chapters_per_volume": "大纲每卷章数",
    "retrieval_enabled": "启用相关章节检索",
    "retrieval_max_chapters": "每章召回的相关章节数",
    "consistency_auto_interval": "自动审校间隔（章）",
}

CONFIG_DEFAULTS = {
    "site_name": "AI 小说工坊", "ai_temperature": "0.8", "ai_http_timeout": "120",
    "context_max_recent_chapters": "5", "context_summary_max_chars": "12000",
    "chapter_target_words": "3000", "outline_volumes": "3",
    "outline_chapters_per_volume": "20", "retrieval_enabled": "1",
    "retrieval_max_chapters": "5", "consistency_auto_interval": "0",
}


@router.get("/config")
def config_index(db: Session = Depends(get_db)):
    rows = {c.key: c.value for c in db.query(SystemConfig).all()}
    return ok({key: rows.get(key, CONFIG_DEFAULTS[key]) for key in CONFIG_KEYS})


@router.put("/config")
def config_update(payload: dict, db: Session = Depends(get_db)):
    values = {key: payload[key] for key in CONFIG_KEYS if key in payload}
    if not values:
        return fail("没有可保存的配置")
    existing = {c.key: c for c in db.query(SystemConfig).all()}
    for key, value in values.items():
        if key in existing:
            existing[key].value = str(value)
            existing[key].updated_at = now()
        else:
            db.add(SystemConfig(key=key, value=str(value), description=CONFIG_KEYS[key], updated_at=now()))
    db.commit()
    return ok(None, "配置已保存")
