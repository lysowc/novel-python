"""相关章节检索（jieba 分词 + BM25，本地零外部依赖）

把"摘要是索引"从"按最近滚动"升级为"按相关性召回"：
生成第 N 章前，用本章大纲目标 + 用户指令 + 小说记忆作为查询，
在全部历史章节摘要上做 BM25 排序，召回已被滚动窗口丢弃的相关早期章节。
"""
from sqlalchemy.orm import Session

from app.models import Chapter, Novel, SystemConfig


def _outline_target(novel: Novel, chapter_no: int) -> str:
    # 惰性导入，避免与 context_builder 模块级循环依赖
    from app.services.context_builder import outline_target
    return outline_target(novel, chapter_no)


def _tokens(text: str) -> list[str]:
    import jieba
    return [t.strip().lower() for t in jieba.lcut(text) if t.strip()]


def get_config_int(db: Session, key: str, default: int) -> int:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if not row or row.value is None or str(row.value) == "":
        return default
    try:
        return int(str(row.value))
    except (TypeError, ValueError):
        return default


def in_context_chapter_nos(db: Session, novel: Novel) -> set[int]:
    """已经处于上下文中的章节号：滚动摘要窗口 + 最近 N 章正文"""
    cap = get_config_int(db, "context_summary_max_chars", 12000)
    recent_n = get_config_int(db, "context_max_recent_chapters", 5)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel.id, Chapter.status == "published")
        .order_by(Chapter.chapter_no.desc())
        .all()
    )
    nos: set[int] = set()
    length = 0
    windowed = 0
    for chapter in chapters:
        line = f"第{chapter.chapter_no}章 {chapter.title}\n" + (chapter.summary or "").strip()
        if length + len(line) > cap and windowed > 0:
            break
        nos.add(chapter.chapter_no)
        length += len(line)
        windowed += 1
    for chapter in chapters[: max(0, recent_n)]:
        nos.add(chapter.chapter_no)
    return nos


def build_query(db: Session, novel: Novel, chapter_no: int, instruction: str = "") -> str:
    parts = []
    if instruction.strip():
        parts.append(instruction.strip())
    parts.append(_outline_target(novel, chapter_no))
    memory = novel.memory
    if memory and (memory.content or "").strip():
        parts.append(memory.to_context_text()[:2000])
    return "\n".join(parts)


def related_chapters(
    db: Session, novel: Novel, chapter_no: int, instruction: str = "",
) -> list[dict]:
    """召回与当前章节最相关的历史章节（BM25 排序）"""
    from rank_bm25 import BM25Okapi

    if get_config_int(db, "retrieval_enabled", 1) <= 0:
        return []
    limit = get_config_int(db, "retrieval_max_chapters", 5)
    if limit <= 0:
        return []

    query_tokens = _tokens(build_query(db, novel, chapter_no, instruction))
    if not query_tokens:
        return []

    exclude = in_context_chapter_nos(db, novel)
    candidates = (
        db.query(Chapter)
        .filter(
            Chapter.novel_id == novel.id,
            Chapter.status == "published",
            Chapter.chapter_no < chapter_no,
        )
        .all()
    )

    docs = []
    for chapter in candidates:
        if chapter.chapter_no in exclude:
            continue
        text = (chapter.title or "").strip() + "\n" + (chapter.summary or "").strip()
        if not text.strip():
            continue
        docs.append({"chapter": chapter, "tokens": _tokens(text)})
    if not docs:
        return []

    bm25 = BM25Okapi([d["tokens"] for d in docs])
    scores = bm25.get_scores(query_tokens)
    ranked = sorted(
        (
            {
                "chapter_no": docs[i]["chapter"].chapter_no,
                "title": docs[i]["chapter"].title,
                "summary": (docs[i]["chapter"].summary or "").strip(),
                "score": round(float(scores[i]), 4),
            }
            for i in range(len(docs))
            if scores[i] > 0
        ),
        key=lambda x: -x["score"],
    )
    return ranked[:limit]


def render_retrieved(retrieved: list[dict]) -> str:
    if not retrieved:
        return ""
    lines = [f"第{item['chapter_no']}章 {item['title']}\n{item['summary']}" for item in retrieved]
    return "\n\n".join(lines)


__all__ = [
    "related_chapters", "render_retrieved", "in_context_chapter_nos",
    "build_query", "get_config_int",
]
