"""AI 续写上下文分层组装（设定 + 记忆 + 摘要 + 检索召回 + 最近正文 + 大纲）

正文是历史，摘要是索引，小说记忆是当前状态。
"""
from sqlalchemy.orm import Session

from app.models import Chapter, Novel, SystemConfig
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


def summaries_text(db: Session, novel: Novel) -> str:
    cap = get_config_int(db, "context_summary_max_chars", 12000)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel.id, Chapter.status == "published")
        .order_by(Chapter.chapter_no.desc())
        .all()
    )
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


def recent_chapters_text(db: Session, novel: Novel) -> str:
    n = get_config_int(db, "context_max_recent_chapters", 5)
    if n <= 0:
        return "（未启用最近章节正文）"
    chapters = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel.id, Chapter.status == "published")
        .order_by(Chapter.chapter_no.desc())
        .limit(n)
        .all()
    )[::-1]
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


def outline_title(novel: Novel, chapter_no: int) -> str:
    outline = novel.get_outline_array()
    for volume in outline.get("volumes", []):
        for chapter in volume.get("chapters", []):
            if int(chapter.get("no") or 0) == chapter_no:
                title = str(chapter.get("title", "")).strip()
                if title:
                    return title
    return f"第{chapter_no}章"


def build_chapter_context(
    db: Session, novel: Novel, chapter_no: int, instruction: str = "", retrieved: list[dict] | None = None,
) -> str:
    # 惰性导入，避免模块级循环依赖
    from app.services.retrieval import render_retrieved

    sections = [
        ("【小说设定】", setting_text(novel)),
        ("【小说记忆（当前状态）】", memory_text(novel)),
        ("【历史章节摘要】", summaries_text(db, novel)),
    ]
    retrieved_text = render_retrieved(retrieved or [])
    if retrieved_text:
        sections.append(("【相关历史章节（检索召回）】", retrieved_text))
    sections.append(("【最近章节正文】", recent_chapters_text(db, novel)))
    sections.append(("【本章大纲】", outline_target(novel, chapter_no)))

    parts = [f"{label}\n{content}" for label, content in sections]
    if instruction.strip():
        parts.append("【用户附加要求】\n" + instruction.strip())
    return "\n\n".join(parts)


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
        .filter(Chapter.novel_id == novel.id, Chapter.status == "published")
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
