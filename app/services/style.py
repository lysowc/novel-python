"""文风提醒：统计最近几章的高频套话、开头和结尾，写新章节时提醒 AI 换个写法（本地统计，不调用 AI）

模型写长篇容易形成固定的"腔调"：同一批动作词（攥、指节、没说话）、大量破折号、
每章都用"时间 + 天色 + 主角在干嘛"开头、用一句悬念短句收尾。光在 prompt 里说"别这样"不够，
把最近几章里实际出现的情况摆给它看，效果更直接。
"""
import re
from collections import Counter

from sqlalchemy.orm import Session

from app.models import CHAPTER_WRITTEN_STATUSES, Chapter, Novel

RECENT_CHAPTERS = 3

# (要数的写法, 给 AI 看的说法, 最近几章平均每章超过多少次就提醒)
TICS: list[tuple[str, str, float]] = [
    ("攥", "攥", 3),
    ("指节", "指节", 1.5),
    ("指尖", "指尖", 2),
    ("指头", "指头", 3),
    ("虎口", "虎口", 2),
    ("喉结", "喉结", 1),
    ("嘴角", "嘴角", 2),
    ("眯起眼", "眯起眼", 1),
    ("瞳孔", "瞳孔", 1),
    ("深吸一口气", "深吸一口气", 1),
    ("吐出一口气", "吐出一口气", 1),
    ("顿了顿", "顿了顿", 1.5),
    ("没说话", "没说话", 1.5),
    ("没吭声", "没吭声", 1.5),
    ("没接话", "没接话", 1.5),
    ("没动", "没动", 2),
    ("沉默", "沉默", 2),
    ("心里一沉", "心里一沉", 1),
    ("心头一紧", "心头一紧", 1),
    ("一字一顿", "一字一顿", 1),
    ("仿佛", "仿佛", 2),
    ("似乎", "似乎", 3),
    ("像是", "像是", 4),
    ("不由得", "不由得", 1.5),
    ("缓缓", "缓缓", 2),
    ("……", "省略号", 4),
]
DASH_LIMIT = 5  # 每章破折号建议上限


def _first_sentence(text: str) -> str:
    text = (text or "").strip()
    m = re.match(r"(.{4,60}?[。！？!?])", text.replace("\n", ""))
    return (m.group(1) if m else text[:50]).strip()


def _last_sentence(text: str) -> str:
    paras = [p.strip() for p in (text or "").strip().split("\n") if p.strip()]
    if not paras:
        return ""
    last = paras[-1]
    parts = [s for s in re.split(r"(?<=[。！？!?])", last) if s.strip()]
    return (parts[-1] if parts else last)[-60:].strip()


def style_hints(db: Session, novel: Novel, chapter_no: int, recent: int = RECENT_CHAPTERS) -> str:
    """最近几章的文风问题，没有明显问题返回空串"""
    rows = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel.id, Chapter.status.in_(CHAPTER_WRITTEN_STATUSES),
                Chapter.chapter_no < chapter_no)
        .order_by(Chapter.chapter_no.desc())
        .limit(recent)
        .all()
    )[::-1]
    rows = [c for c in rows if (c.content or "").strip()]
    if not rows:
        return ""
    n = len(rows)
    text = "\n".join(c.content for c in rows)
    lines: list[str] = []

    dash = text.count("——") / n
    if dash > DASH_LIMIT:
        lines.append(f"- 破折号最近每章平均 {dash:.0f} 个，太多了。本章不超过 {DASH_LIMIT} 个，能用句号、逗号断开的就别用破折号")

    overused = []
    for word, label, limit in TICS:
        avg = text.count(word) / n
        if avg > limit:
            overused.append((avg / limit, f"「{label}」每章约 {avg:.0f} 次"))
    if overused:
        overused.sort(reverse=True)
        lines.append("- 这些词最近用得太多，本章尽量不用，换成别的动作和说法：" + "、".join(t for _, t in overused[:8]))

    shorts = Counter(p.strip() for c in rows for p in c.content.split("\n") if 0 < len(p.strip()) <= 12)
    repeated = [p for p, k in shorts.most_common(10) if k >= 2 and not p.startswith("“")]
    if repeated:
        lines.append("- 这些短句最近反复单独成段，本章不要再用：" + " / ".join(repeated[:5]))

    openings = [f"第{c.chapter_no}章：{_first_sentence(c.content)}" for c in rows]
    endings = [f"第{c.chapter_no}章：{_last_sentence(c.content)}" for c in rows]
    lines.append("- 最近几章是这样开头的，本章换一种开头方式（比如直接从对话、从别的人物、从一件正在发生的事切入）：\n  "
                 + "\n  ".join(openings))
    lines.append("- 最近几章是这样结尾的，本章换一种收法，不要再用同样的动作或同样的悬念短句收尾：\n  "
                 + "\n  ".join(endings))
    return "\n".join(lines)
