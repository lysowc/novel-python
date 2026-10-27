"""相关章节检索（jieba 分词 + BM25，本地零外部依赖）

把"摘要是索引"从"按最近滚动"升级为"按相关性召回"：
生成第 N 章前，用本章大纲目标 + 用户指令 + 小说记忆作为查询，
在全部历史章节摘要上做 BM25 排序，召回已被滚动窗口丢弃的相关早期章节。
"""
from sqlalchemy.orm import Session

from app.models import CHAPTER_WRITTEN_STATUSES, Chapter, Novel, SystemConfig


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


def in_context_chapter_nos(db: Session, novel: Novel, before_no: int | None = None) -> set[int]:
    """已经处于上下文中的章节号：滚动摘要窗口 + 最近 N 章正文（与 context_builder 取同一范围）"""
    window, recent = context_windows(db, novel, before_no)
    return window | recent


def context_windows(db: Session, novel: Novel, before_no: int | None = None) -> tuple[set[int], set[int]]:
    """(摘要窗口内的章号, 最近正文窗口内的章号)"""
    cap = get_config_int(db, "context_summary_max_chars", 12000)
    recent_n = get_config_int(db, "context_max_recent_chapters", 5)
    q = db.query(Chapter).filter(Chapter.novel_id == novel.id, Chapter.status.in_(CHAPTER_WRITTEN_STATUSES))
    if before_no is not None:
        q = q.filter(Chapter.chapter_no < before_no)
    chapters = q.order_by(Chapter.chapter_no.desc()).all()
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
    recent = {chapter.chapter_no for chapter in chapters[: max(0, recent_n)]}
    return nos, recent


def build_query(db: Session, novel: Novel, chapter_no: int, instruction: str = "",
                memory_text: str | None = None) -> str:
    parts = []
    if instruction.strip():
        parts.append(instruction.strip())
    parts.append(_outline_target(novel, chapter_no))
    if memory_text is None:
        memory = novel.memory
        memory_text = memory.to_context_text() if memory and (memory.content or "").strip() else ""
    if memory_text.strip():
        parts.append(memory_text[:2000])
    return "\n".join(parts)


def related_chapters(
    db: Session, novel: Novel, chapter_no: int, instruction: str = "", memory_text: str | None = None,
) -> list[dict]:
    """召回与当前章节最相关的历史章节（BM25 排序）"""
    from rank_bm25 import BM25Okapi

    if get_config_int(db, "retrieval_enabled", 1) <= 0:
        return []
    limit = get_config_int(db, "retrieval_max_chapters", 5)
    if limit <= 0:
        return []

    query_tokens = _tokens(build_query(db, novel, chapter_no, instruction, memory_text))
    if not query_tokens:
        return []

    # 摘要窗口内的章节摘要已在上下文里，但正文细节不在：它们仍可通过"正文片段"被召回；
    # 最近 N 章正文已全文在上下文里，完全排除
    summary_window, recent_window = context_windows(db, novel, chapter_no)
    candidates = (
        db.query(Chapter)
        .filter(
            Chapter.novel_id == novel.id,
            Chapter.status.in_(CHAPTER_WRITTEN_STATUSES),
            Chapter.chapter_no < chapter_no,
        )
        .all()
    )

    docs = []
    for chapter in candidates:
        if chapter.chapter_no in recent_window:
            continue
        text = (chapter.title or "").strip() + "\n" + (chapter.summary or "").strip()
        docs.append({"chapter": chapter, "tokens": _tokens(text),
                     "in_summaries": chapter.chapter_no in summary_window})
    if not docs:
        return []

    # 1) 摘要级 BM25：章节整体相关性（摘要已在上下文里的章节不参与）
    summary_scores = BM25Okapi([d["tokens"] for d in docs]).get_scores(query_tokens)
    for i, d in enumerate(docs):
        if d["in_summaries"]:
            summary_scores[i] = 0

    # 2) 正文片段级 BM25：摘要写得粗时，靠正文细节（道具、地名、人名）召回
    passages = []  # (doc_index, passage_text, tokens)
    for di, d in enumerate(docs):
        for text, tokens in _chapter_passages(d["chapter"]):
            passages.append((di, text, tokens))
    best_passage: dict[int, tuple[float, str]] = {}
    if passages:
        p_scores = BM25Okapi([p[2] for p in passages]).get_scores(query_tokens)
        for (di, text, _), score in zip(passages, p_scores):
            if score > 0 and score > best_passage.get(di, (0.0, ""))[0]:
                best_passage[di] = (float(score), text)

    # 3) 倒数排名融合（RRF）：两种分数量纲不同，按名次融合更稳
    def ranks(score_of):
        order = sorted((i for i in range(len(docs)) if score_of(i) > 0), key=lambda i: -score_of(i))
        return {i: r for r, i in enumerate(order)}
    r_sum = ranks(lambda i: float(summary_scores[i]))
    r_pas = ranks(lambda i: best_passage.get(i, (0.0, ""))[0])
    K = 60
    fused = {i: (1 / (K + r_sum[i]) if i in r_sum else 0) + (1 / (K + r_pas[i]) if i in r_pas else 0)
             for i in set(r_sum) | set(r_pas)}

    ranked = sorted(
        (
            {
                "chapter_no": docs[i]["chapter"].chapter_no,
                "title": docs[i]["chapter"].title,
                "summary": (docs[i]["chapter"].summary or "").strip(),
                "excerpt": best_passage.get(i, (0.0, ""))[1],
                "in_summaries": docs[i]["in_summaries"],
                "score": round(fused[i] * 1000, 4),
            }
            for i in fused
        ),
        key=lambda x: -x["score"],
    )
    return ranked[:limit]


# 正文分段的分词结果缓存（worker 进程内；章节内容变化时 updated_at 变化，自动失效）
_PASSAGE_CACHE: dict[tuple, list[tuple[str, list[str]]]] = {}
_PASSAGE_CACHE_MAX = 5000
PASSAGE_CHARS = 400


def _chapter_passages(chapter: Chapter) -> list[tuple[str, list[str]]]:
    key = (chapter.id, str(chapter.updated_at), len(chapter.content or ""))
    hit = _PASSAGE_CACHE.get(key)
    if hit is not None:
        return hit
    paras = [p.strip() for p in (chapter.content or "").splitlines() if p.strip()]
    chunks, buf = [], ""
    for para in paras:
        if buf and len(buf) + len(para) > PASSAGE_CHARS:
            chunks.append(buf)
            buf = ""
        buf = (buf + "\n" + para) if buf else para
    if buf:
        chunks.append(buf)
    result = [(c[:PASSAGE_CHARS * 2], _tokens(c)) for c in chunks]
    if len(_PASSAGE_CACHE) >= _PASSAGE_CACHE_MAX:
        _PASSAGE_CACHE.clear()
    _PASSAGE_CACHE[key] = result
    return result


def render_retrieved(retrieved: list[dict]) -> str:
    if not retrieved:
        return ""
    lines = []
    for item in retrieved:
        excerpt = (item.get("excerpt") or "").strip()
        if len(excerpt) > 300:
            excerpt = excerpt[:300] + "……"
        if item.get("in_summaries"):
            # 摘要已在【历史章节摘要】里，只补原文细节
            if not excerpt:
                continue
            lines.append(f"第{item['chapter_no']}章 {item['title']}\n相关原文片段：{excerpt}")
            continue
        line = f"第{item['chapter_no']}章 {item['title']}\n{item['summary']}"
        if excerpt:
            line += f"\n相关原文片段：{excerpt}"
        lines.append(line)
    return "\n\n".join(lines)


__all__ = [
    "related_chapters", "render_retrieved", "in_context_chapter_nos",
    "build_query", "get_config_int",
]
