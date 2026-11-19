"""后台 API：点子与点子聊天"""
import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import dt_str, fail, json_dumps, now, ok
from app.models import (
    Idea, IdeaChat, Novel, NovelSetting,
)
from app.services import prompt_service, task_service
from app.services.ai_service import DECISIONS_RULES, decisions_text
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


IDEA_MODES = ("refine", "explore")


@router.post("/ideas")
def idea_store(payload: dict, db: Session = Depends(get_db)):
    mode = str(payload.get("mode") or "refine")
    if mode not in IDEA_MODES:
        mode = "refine"
    title = str(payload.get("title") or "").strip()
    if not title:
        if mode != "explore":
            return fail("点子标题不能为空")
        title = "随便聊聊 · " + now().strftime("%m-%d %H:%M")  # 还没有点子时不用先起标题
    i = Idea(
        category_id=int(payload.get("category_id") or 0) or None, title=title[:255],
        content=str(payload.get("content") or "").strip(), status="unused", mode=mode,
        decisions=str(payload.get("decisions") or "").strip() or None,
        source_idea_id=int(payload.get("source_idea_id") or 0) or None,
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
    if payload.get("mode") in IDEA_MODES:
        i.mode = payload["mode"]
    if payload.get("decisions") is not None:
        i.decisions = str(payload.get("decisions")).strip() or None
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
            from app.services import budget
            budget.ensure(db2)  # 超出每日费用上限就不再调用 AI
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
            prompt_type = "idea_explore" if idea_row.mode == "explore" else "idea_chat"
            if idea_row.mode == "explore":
                background = "作者还没有明确的点子，想先随便聊聊。" + (f"大致方向：{category}。" if category else "")
            msgs.insert(0, {"role": "system", "content": prompt_service.render(db2, prompt_type) + "\n\n" + background})
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


@router.get("/ideas/{iid}")
def idea_show(iid: int, db: Session = Depends(get_db)):
    i = db.query(Idea).filter(Idea.id == iid).first()
    if not i:
        return fail("点子不存在")
    return ok(f_idea(i))


EXTRACT_SYSTEM = (
    "你是一位小说策划编辑。下面是作者和 AI 随便聊小说点子的记录。请把讨论里出现过、值得写的小说点子提炼出来。\n"
    "要求：\n"
    "- 只提炼讨论里真的出现过的方向，作者表现出兴趣、追问过、补充过的优先；不要自己编新的\n"
    "- 1~5 个，按作者的兴趣从高到低排\n"
    "- 讨论里作者补充过的细节、改过的地方要写进去，以作者的说法为准\n"
    "- 每个点子：title 不超过 20 字；content 150~300 字，写清核心创意、主角和处境、好玩在哪、大概怎么往下走\n"
    "- 每个点子再列 decisions：作者针对这个点子已经拍板的事。规则：\n"
    + DECISIONS_RULES + "\n"
    "  只聊了一两句、作者没定过什么的点子，decisions 给空数组\n"
    "只输出 JSON：{\"ideas\": [{\"title\": \"\", \"content\": \"\", \"decisions\": [\"\"]}]}"
)


@router.post("/ideas/{iid}/extract")
def idea_extract(iid: int, db: Session = Depends(get_db)):
    """从「随便聊聊」的讨论里提炼出几个点子（只返回，不保存；作者挑了再用 POST /ideas 存）"""
    from app.services import budget
    from app.services.ai_client import AiClient
    from app.services.ai_service import extract_json

    idea = db.query(Idea).filter(Idea.id == iid).first()
    if not idea:
        return fail("点子不存在")
    chats = db.query(IdeaChat).filter(IdeaChat.idea_id == iid).order_by(IdeaChat.id).all()
    if not chats:
        return fail("还没有聊天内容")
    try:
        budget.ensure(db)
    except Exception as e:  # noqa: BLE001  超出每日费用上限
        return fail(str(e))
    transcript = "\n".join(("作者：" if c.role == "user" else "AI：") + c.content for c in chats)
    if len(transcript) > 30000:
        transcript = transcript[:5000] + "\n……（中间讨论省略）……\n" + transcript[-25000:]
    try:
        result = AiClient(db).chat(
            [{"role": "system", "content": EXTRACT_SYSTEM}, {"role": "user", "content": f"讨论记录：\n{transcript}"}],
            {"task_type": "idea_extract", "max_tokens": 4096},
        )
    except Exception as e:  # noqa: BLE001
        return fail(f"提炼失败：{e}")
    data = extract_json(result["text"])
    items = []
    for it in data.get("ideas") or [] if isinstance(data, dict) else []:
        if isinstance(it, dict) and str(it.get("title") or "").strip() and str(it.get("content") or "").strip():
            items.append({"title": str(it["title"]).strip()[:60], "content": str(it["content"]).strip(),
                          "decisions": decisions_text(it.get("decisions"))})
    if not items:
        return fail("没能从讨论里提炼出点子，再多聊几句试试")
    return ok({"ideas": items[:5]})


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
    if len(transcript) > 30000:
        transcript = transcript[:5000] + "\n……（中间讨论省略）……\n" + transcript[-25000:]

    title, content, decisions = "", "", ""
    try:
        from app.services.ai_service import extract_json
        system = (
            "你是一位网文策划编辑。请根据下面的讨论记录，提炼出小说点子。\n"
            "title：标题（20 字以内）；content：点子内容（200~400 字，包含核心创意、主角设定、世界观、卖点）；\n"
            "decisions：作者在讨论里已经拍板的事。规则：\n" + DECISIONS_RULES + "\n"
            "只输出 JSON：{\"title\": \"\", \"content\": \"\", \"decisions\": [\"\"]}"
        )
        result = AiClient(db).chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": f"讨论记录：\n{transcript}"}],
            {"task_type": "idea_save", "max_tokens": 4096},
        )
        data = extract_json(result["text"])
        if data:
            title = str(data.get("title") or "").strip()
            content = str(data.get("content") or "").strip()
            decisions = decisions_text(data.get("decisions"))
        else:  # 兼容：模型没按 JSON 返回，按「第一行标题、其余内容」解析
            lines = [l.strip() for l in result["text"].strip().split("\n") if l.strip()]
            title = lines[0] if lines else ""
            content = "\n".join(lines[1:])
        title = re.sub(r"^(标题|书名)[:：]\s*", "", title)
    except Exception:
        title, content, decisions = "", "", ""

    if not title or not content:
        last_user = next((c for c in reversed(chats) if c.role == "user"), None)
        fallback = last_user.content.strip() if last_user else ""
        if not title:
            title = fallback[:20] or "未命名点子"
        if not content:
            content = fallback or transcript

    idea.title = title
    idea.content = content
    if decisions:
        idea.decisions = decisions
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
    if (idea.decisions or "").strip():
        # 讨论里拍板的事直接进设定，生成设定/大纲/正文都照它来
        db.add(NovelSetting(novel_id=n.id, decisions=idea.decisions.strip(), created_at=now(), updated_at=now()))
    idea.novel_id = n.id
    idea.status = "used"
    idea.updated_at = now()
    db.commit()
    task = task_service.create(db, "generate_setting", n.id)
    return ok({"novel_id": n.id, "task_id": task.id}, "小说已创建，AI 正在生成设定")
