"""后台 API：点子与点子聊天"""
import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import dt_str, fail, json_dumps, now, ok
from app.models import (
    Idea, IdeaChat, Novel,
)
from app.services import prompt_service, task_service
from app.routers.admin.formatters import f_idea

router = APIRouter(prefix="/api/admin", tags=["admin"])


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

    def build_messages() -> list[dict]:
        """读取点子与历史消息，组装发给 AI 的消息（在线程里执行，避免阻塞事件循环）"""
        db2 = _open_db()
        try:
            idea_row = db2.query(Idea).filter(Idea.id == iid).first()
            history_row = db2.query(IdeaChat).filter(IdeaChat.idea_id == iid).order_by(IdeaChat.id).all()
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
            msgs.insert(0, {"role": "system", "content": prompt_service.render(db2, "idea_chat") + "\n\n" + background})
            return msgs
        finally:
            db2.close()

    def save_reply(text: str) -> None:
        db2 = _open_db()
        try:
            db2.add(IdeaChat(idea_id=iid, role="assistant", content=text, created_at=now()))
            db2.commit()
        finally:
            db2.close()

    async def generate_stream():
        """异步 SSE：等待 AI 输出时不占用线程池线程（AI 调用本身在独立线程里跑）"""
        import asyncio
        import threading

        from app.services.ai_client import AiClient

        try:
            msgs = await asyncio.to_thread(build_messages)
            loop = asyncio.get_running_loop()
            q: asyncio.Queue = asyncio.Queue()

            def put(item):
                loop.call_soon_threadsafe(q.put_nowait, item)

            def run_ai():
                ai_db = _open_db()
                try:
                    result = AiClient(ai_db).chat_stream(
                        msgs, {"task_type": "idea_chat"},
                        on_delta=lambda d: put(("delta", d)),
                    )
                    put(("result", result))
                except Exception as e:
                    put(("error", str(e)))
                finally:
                    ai_db.close()

            threading.Thread(target=run_ai, daemon=True).start()

            full = ""
            while True:
                kind, payload = await q.get()
                if kind == "delta":
                    full += payload
                    yield "data: " + json_dumps({"type": "delta", "content": payload}) + "\n\n"
                elif kind == "error":
                    # 出错时只发 error，不再发 done（避免前端把错误状态冲掉）
                    yield "data: " + json_dumps({"type": "error", "msg": payload}) + "\n\n"
                    return
                else:
                    break

            full = full.strip()
            if full:
                await asyncio.to_thread(save_reply, full)
            yield "data: " + json_dumps({"type": "done"}) + "\n\n"
        except Exception as e:
            yield "data: " + json_dumps({"type": "error", "msg": str(e)}) + "\n\n"

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
