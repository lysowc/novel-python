"""AI 续写上下文分层组装（设定 + 记忆 + 摘要 + 检索召回 + 最近正文 + 大纲）

正文是历史，摘要是索引，小说记忆是当前状态。
"""
from sqlalchemy.orm import Session

from app.models import CHAPTER_WRITTEN_STATUSES, Chapter, Novel
from app.services import memory as memory_service
from app.services.retrieval import get_config_int


def setting_text(novel: Novel) -> str:
    setting = novel.setting
    if not setting or setting.is_empty():
        description = (novel.description or "").strip()
        return f"【小说简介】{description}\n（其余设定尚未生成）"
    return setting.to_context_text()


def memory_text(novel: Novel) -> str:
    memory = novel.memory
    if not memory or not (memory.content or "").strip():
        return "（暂无小说记忆）"
    return memory_service.to_context_text(memory.content)


def published_chapters(db: Session, novel: Novel, before_no: int | None = None):
    """已写章节（含草稿，章号倒序）；指定 before_no 时只取它之前的章节（生成/重写第 N 章时不能看到第 N 章及之后）"""
    q = db.query(Chapter).filter(Chapter.novel_id == novel.id, Chapter.status.in_(CHAPTER_WRITTEN_STATUSES))
    if before_no is not None:
        q = q.filter(Chapter.chapter_no < before_no)
    return q.order_by(Chapter.chapter_no.desc())


def summaries_text(db: Session, novel: Novel, before_no: int | None = None, cap: int | None = None) -> str:
    if cap is None:
        cap = get_config_int(db, "context_summary_max_chars", 12000)
    chapters = published_chapters(db, novel, before_no).all()
    lines = []
    length = 0
    for chapter in chapters:
        line = f"第{chapter.chapter_no}章 {chapter.title}\n" + (chapter.summary or "（无摘要）").strip()
        if length + len(line) > cap and lines:
            break
        lines.append(line)
        length += len(line)
    if not lines:
        return "（暂无历史章节）"
    return "\n\n".join(reversed(lines))


def recent_chapters_text(db: Session, novel: Novel, before_no: int | None = None, limit: int | None = None) -> str:
    n = get_config_int(db, "context_max_recent_chapters", 5) if limit is None else limit
    if n <= 0:
        return "（未启用最近章节正文）"
    chapters = published_chapters(db, novel, before_no).limit(n).all()[::-1]
    if not chapters:
        return "（暂无正文）"
    parts = [f"第{c.chapter_no}章 {c.title}\n\n{c.content}" for c in chapters]
    return "\n\n---\n\n".join(parts)


def outline_target(novel: Novel, chapter_no: int) -> str:
    outline = novel.get_outline_array()
    for volume in outline.get("volumes", []):
        for chapter in volume.get("chapters", []):
            if int(chapter.get("no") or 0) == chapter_no:
                return f"第{chapter_no}章 {chapter.get('title', '')}\n本章目标：{chapter.get('summary', '')}"
    return f"第{chapter_no}章（大纲未覆盖此章，请根据剧情走向自由创作，并自拟合适的情节）"


def outline_max_no(novel: Novel) -> int:
    """大纲规划到的最大章号（0 = 还没有大纲）"""
    nos = [int(c.get("no") or 0) for v in novel.get_outline_array().get("volumes", [])
           for c in (v.get("chapters") or []) if isinstance(c, dict)]
    return max(nos) if nos else 0


def outline_status(db: Session, novel: Novel) -> dict:
    """大纲余量：剩余章数 ≤ 阈值时提醒（或自动续写大纲）"""
    from sqlalchemy import func
    written = int(db.query(func.max(Chapter.chapter_no)).filter(Chapter.novel_id == novel.id).scalar() or 0)
    planned = outline_max_no(novel)
    threshold = get_config_int(db, "outline_warn_remaining", 3)
    remaining = planned - written
    return {
        "outline_max": planned, "written_max": written, "remaining": remaining,
        "threshold": threshold, "warn": planned > 0 and remaining <= threshold,
    }


def outline_title(novel: Novel, chapter_no: int) -> str:
    outline = novel.get_outline_array()
    for volume in outline.get("volumes", []):
        for chapter in volume.get("chapters", []):
            if int(chapter.get("no") or 0) == chapter_no:
                title = str(chapter.get("title", "")).strip()
                if title:
                    return title
    return f"第{chapter_no}章"


# 中文正文约 1 字 ≈ 1 token（各家分词器在 0.6~1.5 之间，取 1 偏保守）；预留给 system prompt 的 tokens
CHARS_PER_TOKEN = 1.0
SYSTEM_PROMPT_RESERVE = 2000


def context_budget(db: Session, output_tokens: int = 0) -> int:
    """上下文字符上限：系统设置 context_max_chars 与「写作模型上下文窗口 - 输出预留」取较小值"""
    budget = get_config_int(db, "context_max_chars", 60000)
    from app.services.ai_client import resolve_target
    target = resolve_target(db, "continue_chapter")  # 正文用的写作模型
    window = int(target[1].context_window or 0) if target else 0
    if window > 0:
        derived = int((window - max(0, output_tokens) - SYSTEM_PROMPT_RESERVE) * CHARS_PER_TOKEN)
        derived = max(4000, derived)  # 窗口填得过小时也至少留够设定 + 大纲
        budget = min(budget, derived) if budget > 0 else derived
    return budget


def build_chapter_context(
    db: Session, novel: Novel, chapter_no: int, instruction: str = "", retrieved: list[dict] | None = None,
    memory: str | None = None, output_tokens: int = 0,
) -> str:
    """组装第 chapter_no 章的生成上下文：摘要/正文只取本章之前的章节；
    memory 为"本章开始前"的记忆文本（见 memory_store.memory_before），不传则用全书最新记忆"""
    # 惰性导入，避免模块级循环依赖
    from app.services.retrieval import render_retrieved

    budget = context_budget(db, output_tokens)
    fixed = {
        "setting": setting_text(novel),
        "memory": memory if memory is not None else memory_text(novel),
        "outline": outline_target(novel, chapter_no),
        "instruction": instruction.strip(),
    }
    retrieved = list(retrieved or [])
    recent_n = get_config_int(db, "context_max_recent_chapters", 5)
    summary_cap = get_config_int(db, "context_summary_max_chars", 12000)

    def assemble() -> str:
        sections = [
            ("【小说设定】", fixed["setting"]),
            ("【小说记忆（当前状态）】", fixed["memory"]),
            ("【历史章节摘要】", summaries_text(db, novel, chapter_no, summary_cap)),
        ]
        retrieved_text = render_retrieved(retrieved)
        if retrieved_text:
            sections.append(("【相关历史章节（检索召回）】", retrieved_text))
        sections.append(("【最近章节正文】", recent_chapters_text(db, novel, chapter_no, recent_n)))
        sections.append(("【本章大纲】", fixed["outline"]))
        parts = [f"{label}\n{content}" for label, content in sections]
        if fixed["instruction"]:
            parts.append("【用户附加要求】\n" + fixed["instruction"])
        return "\n\n".join(parts)

    # 总长度预算：超出时依次压缩 最近正文（至少留 1 章）→ 召回章节 → 摘要窗口，设定/记忆/大纲不动
    text = assemble()
    while budget > 0 and len(text) > budget:
        if recent_n > 1:
            recent_n -= 1
        elif retrieved:
            retrieved.pop()
        elif summary_cap > 1000:
            summary_cap = max(1000, int(summary_cap * 0.6))
        else:
            break
        text = assemble()
    return text


def recent_text_sample(db: Session, novel: Novel, chapters: int = 3, per_chapter: int = 4000) -> str:
    """一致性审校用：最近几章正文（每章截断），供检查正文层面的细节矛盾"""
    rows = published_chapters(db, novel).limit(max(1, chapters)).all()[::-1]
    parts = []
    for c in rows:
        body = (c.content or "").strip()
        if len(body) > per_chapter:
            body = body[:per_chapter] + "\n……（截断）"
        if body:
            parts.append(f"第{c.chapter_no}章 {c.title}\n{body}")
    return "\n\n---\n\n".join(parts)


def outline_text(novel: Novel) -> str:
    outline = novel.get_outline_array()
    volumes = outline.get("volumes", [])
    if not volumes:
        return "（大纲尚未生成）"
    parts = []
    for volume in volumes:
        parts.append(f"【{volume.get('title', '')}】")
        for chapter in volume.get("chapters", []):
            no = int(chapter.get("no") or 0)
            ct = str(chapter.get("title", "")).strip()
            cs = str(chapter.get("summary", "")).strip()
            if no > 0:
                parts.append(f"第{no}章 {ct}" + (f"：{cs}" if cs else ""))
            else:
                parts.append((ct + (f"：{cs}" if cs else "")).strip())
    return "\n".join(parts)


def written_progress_text(db: Session, novel: Novel, max_chars: int = 20000) -> str:
    chapters = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel.id, Chapter.status.in_(CHAPTER_WRITTEN_STATUSES))
        .order_by(Chapter.chapter_no.desc())
        .all()
    )
    if not chapters:
        return "（还没有已写章节）"

    def line_for(c: Chapter) -> str:
        return f"第{c.chapter_no}章 {c.title}：" + (c.summary or "（无摘要）").strip()

    lines = [line_for(c) for c in reversed(chapters[:30])]
    used = sum(len(l) for l in lines)

    older = chapters[30:]
    older_count = len(older)
    remaining = max_chars - used
    if older_count > 0 and remaining > 0:
        per_line = 160
        budget_count = max(1, remaining // per_line)
        step = max(1, -(-older_count // budget_count))
        sampled = older[::step]
        for chapter in reversed(sampled):
            line = line_for(chapter)
            if sum(len(l) for l in lines) + len(line) > max_chars:
                lines.append("……（更早章节已省略）")
                break
            lines.append(line)
    return "\n".join(lines)
