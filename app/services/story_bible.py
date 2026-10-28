"""长篇资料库：人物档案、名词表、分段总结

- 人物档案：长期稳定的人设（外貌、性格、能力、境界及其变化、关系）。记忆里的「人物状态」只记近况，
  档案记"这个人是谁"。写某章时只带入本章可能出场的人物，几百个角色也不会把上下文撑爆。
- 名词表：地名、功法、物品、势力等专有名词的统一说法，防止前后写法不一。
- 分段总结：每 N 章一段的前情提要。章节摘要窗口只装得下最近几十章，更早的剧情靠它记住。
"""
import re
from sqlalchemy.orm import Session

from app.helpers import now
from app.models import CHAPTER_WRITTEN_STATUSES, ArcSummary, Chapter, CharacterProfile, Novel, StoryTerm
from app.services import memory as memory_service
from app.services.retrieval import get_config_int

PROFILE_FIELDS = ("role", "appearance", "personality", "abilities", "relationships", "status", "notes")
PROFILE_CONTEXT_LIMIT = 12
TERM_CONTEXT_LIMIT = 30
PROTAGONIST_ROLES = ("主角", "男主", "女主", "protagonist")


def _clip(value, n: int) -> str:
    return str(value or "").strip()[:n]


# ---------- 人物档案 ----------

def profiles(db: Session, novel_id: int) -> list[CharacterProfile]:
    return db.query(CharacterProfile).filter(CharacterProfile.novel_id == novel_id) \
        .order_by(CharacterProfile.first_chapter, CharacterProfile.id).all()


def find_profile(rows: list[CharacterProfile], name: str) -> CharacterProfile | None:
    name = name.strip()
    for p in rows:
        if p.name == name:
            return p
    for p in rows:
        if name in p.names():
            return p
    return None


def realm_at(profile: CharacterProfile, chapter_no: int | None) -> str:
    """第 chapter_no 章开始前的境界（重写中间章节时不能用后面才达到的境界）"""
    history = [h for h in (profile.realm_history or []) if isinstance(h, dict)]
    if chapter_no is None or not history:
        return profile.realm or ""
    before = [h for h in history if int(h.get("chapter") or 0) < chapter_no]
    return str(before[-1].get("realm", "")) if before else ""


def upsert_profiles(db: Session, novel_id: int, chapter_no: int, items) -> list[str]:
    """应用 AI 给出的人物档案更新：新人物建档；已有人物只更新给出的非空字段；境界变化记入历史。
    作者锁定（locked=1）的档案 AI 不改，只记录最近出场章节。"""
    notes = []
    rows = profiles(db, novel_id)
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = _clip(item.get("name"), 64)
        if not name:
            continue
        p = find_profile(rows, name)
        if p is None:
            p = CharacterProfile(novel_id=novel_id, name=name, aliases="", realm="", status="", role="",
                                 realm_history=[], first_chapter=chapter_no, last_chapter=chapter_no,
                                 locked=0, created_at=now())
            db.add(p)
            rows.append(p)
            notes.append("新建档案：" + name)
        p.last_chapter = max(int(p.last_chapter or 0), chapter_no)
        p.updated_at = now()
        if p.locked:
            continue
        aliases = item.get("aliases")
        if isinstance(aliases, list):
            aliases = ",".join(str(a) for a in aliases)
        if aliases:
            merged = list(dict.fromkeys(
                p.names()[1:] + [a.strip() for a in str(aliases).replace("，", ",").split(",") if a.strip()]))
            p.aliases = ",".join(a for a in merged if a != p.name)[:255]
        for field in PROFILE_FIELDS:
            v = str(item.get(field) or "").strip()
            if v:
                setattr(p, field, v[:32] if field == "role" else (v[:255] if field == "status" else v[:1000]))
        realm = _clip(item.get("realm"), 128)
        if realm and realm != realm_at(p, chapter_no + 1):
            # 按章号插入（重写中间章节时，后面章节的境界记录保持原位）
            history = [h for h in (p.realm_history or [])
                       if isinstance(h, dict) and int(h.get("chapter") or 0) != chapter_no]
            history.append({"chapter": chapter_no, "realm": realm})
            history.sort(key=lambda h: int(h.get("chapter") or 0))
            p.realm_history = history[-30:]
            old = p.realm or ""
            p.realm = str(p.realm_history[-1].get("realm", ""))
            if old and p.realm != old:
                notes.append(f"境界变化：{p.name} {old} → {p.realm}")
    db.flush()
    return notes


def mark_appearances(db: Session, novel_id: int, chapter_no: int, content: str) -> int:
    """按正文实际出现的名字/别名更新「最近出场章」。
    AI 的记忆补丁只列出有变化的人物，常驻角色（主角等）每章都在却未必被提到，只靠补丁会停在很早的章节，
    挑选「本章相关人物」时就会被挤掉。这里直接看正文，单字别名容易误判，忽略。"""
    if not content:
        return 0
    count = 0
    for p in profiles(db, novel_id):
        if (p.first_chapter or 0) > chapter_no:
            continue
        if any(len(n) >= 2 and n in content for n in p.names()):
            if int(p.last_chapter or 0) < chapter_no:
                p.last_chapter = chapter_no
                count += 1
    db.flush()
    return count


DEAD_PATTERN = re.compile(r"已死|身亡|死亡|自尽|毙命|阵亡|殒命|被杀|已故|去世|丧命|遇害|伏诛")
NOT_DEAD_PATTERN = re.compile(r"假死|诈死|未死|没死|险些|差点|险死|重伤未死|死里逃生|生死未卜|下落不明")


def is_dead(status: str) -> bool:
    status = status or ""
    return bool(DEAD_PATTERN.search(status)) and not NOT_DEAD_PATTERN.search(status)


def dead_profiles(db: Session, novel_id: int, before_no: int | None = None) -> list[CharacterProfile]:
    """档案状态写着已死亡的人物。before_no：只算本章之前登场的（重写中间章节时不提前"剧透"后面才死的人）"""
    rows = [p for p in profiles(db, novel_id) if is_dead(p.status)]
    if before_no is not None:
        rows = [p for p in rows if (p.last_chapter or 0) < before_no]
    return rows


def relevant_profiles(db: Session, novel: Novel, chapter_no: int, hint_text: str,
                      active_names: list[str] | None = None, limit: int = PROFILE_CONTEXT_LIMIT) -> list[CharacterProfile]:
    """本章可能出场的人物：主角 + 在大纲/附加要求/上一章结尾/记忆活跃人物中提到的 + 最近出场的"""
    rows = [p for p in profiles(db, novel.id) if (p.first_chapter or 0) < chapter_no or p.locked]
    if not rows:
        return []
    picked: list[CharacterProfile] = []

    def add(p):
        if p not in picked:
            picked.append(p)

    for p in rows:
        if any(r in (p.role or "") for r in PROTAGONIST_ROLES):
            add(p)
    for p in rows:
        if any(n and n in hint_text for n in p.names()):
            add(p)
    for name in active_names or []:
        p = find_profile(rows, name)
        if p:
            add(p)
    for p in sorted(rows, key=lambda r: r.last_chapter or 0, reverse=True):
        if len(picked) >= limit:
            break
        add(p)
    return picked[:limit]


def render_profiles(rows: list[CharacterProfile], chapter_no: int | None = None) -> str:
    lines = []
    for p in rows:
        head = p.name + (f"（{p.role}）" if p.role else "")
        if p.aliases:
            head += f" 又称：{p.aliases}"
        bits = []
        for label, value in (("外貌", p.appearance), ("性格", p.personality), ("能力", p.abilities),
                             ("境界", realm_at(p, chapter_no)), ("关系", p.relationships),
                             ("状态", p.status), ("备注", p.notes)):
            value = str(value or "").strip()
            if value:
                bits.append(f"{label}：{value}")
        lines.append("- " + head + ("\n  " + "；".join(bits) if bits else ""))
    return "\n".join(lines)


def profile_brief(db: Session, novel_id: int, limit: int = 80) -> str:
    """更新记忆时给 AI 看的档案索引（姓名 + 别名 + 境界），让它知道谁已建档、境界是否变了"""
    rows = sorted(profiles(db, novel_id), key=lambda r: r.last_chapter or 0, reverse=True)[:limit]
    if not rows:
        return "（暂无人物档案）"
    return "\n".join(
        f"- {p.name}" + (f"（又称 {p.aliases}）" if p.aliases else "") + (f" 境界：{p.realm}" if p.realm else "")
        + (f" 状态：{p.status}" if p.status else "")
        for p in rows)


# ---------- 名词表 ----------

def terms(db: Session, novel_id: int) -> list[StoryTerm]:
    return db.query(StoryTerm).filter(StoryTerm.novel_id == novel_id).order_by(StoryTerm.first_chapter, StoryTerm.id).all()


def upsert_terms(db: Session, novel_id: int, chapter_no: int, items) -> list[str]:
    notes = []
    existing = {t.term: t for t in terms(db, novel_id)}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            item = {"term": item}
        if not isinstance(item, dict):
            continue
        term = _clip(item.get("term") or item.get("name"), 64)
        if not term:
            continue
        desc = str(item.get("description") or "").strip()[:500]
        type_ = _clip(item.get("type"), 32)
        t = existing.get(term)
        if t is None:
            t = StoryTerm(novel_id=novel_id, term=term, type=type_, description=desc,
                          first_chapter=chapter_no, locked=0, created_at=now(), updated_at=now())
            db.add(t)
            existing[term] = t
            notes.append("新名词：" + term)
        elif not t.locked:
            if desc and len(desc) >= len(t.description or "") // 2:
                t.description = desc
            if type_ and not t.type:
                t.type = type_
            t.updated_at = now()
    db.flush()
    return notes


def relevant_terms(db: Session, novel: Novel, chapter_no: int, hint_text: str,
                   limit: int = TERM_CONTEXT_LIMIT) -> list[StoryTerm]:
    rows = [t for t in terms(db, novel.id) if (t.first_chapter or 0) < chapter_no or t.locked]
    mentioned = [t for t in rows if t.term in hint_text]
    rest = [t for t in reversed(rows) if t not in mentioned]
    return (mentioned + rest)[:limit]


def render_terms(rows: list[StoryTerm]) -> str:
    return "\n".join(
        f"- {t.term}" + (f"【{t.type}】" if t.type else "") + (f"：{t.description}" if t.description else "")
        for t in rows)


def term_brief(db: Session, novel_id: int, limit: int = 150) -> str:
    rows = terms(db, novel_id)[-limit:]
    return "、".join(t.term for t in rows) if rows else "（暂无）"


# ---------- 重建时清理 ----------

def reset_from(db: Session, novel_id: int, start_no: int) -> None:
    """从第 start_no 章起重算记忆前：删掉 AI 在这些章节才建的档案/名词，撤回这些章节的境界变化。
    作者锁定的条目不动。"""
    for p in profiles(db, novel_id):
        if p.locked:
            continue
        if (p.first_chapter or 0) >= start_no:
            db.delete(p)
            continue
        history = [h for h in (p.realm_history or []) if isinstance(h, dict) and int(h.get("chapter") or 0) < start_no]
        if history != (p.realm_history or []):
            p.realm_history = history
            p.realm = str(history[-1].get("realm", "")) if history else ""
        if (p.last_chapter or 0) >= start_no:
            p.last_chapter = max(p.first_chapter or 0, start_no - 1)
    for t in terms(db, novel_id):
        if not t.locked and (t.first_chapter or 0) >= start_no:
            db.delete(t)
    db.flush()


# ---------- 分段总结 ----------

def arc_size(db: Session) -> int:
    return max(0, get_config_int(db, "arc_summary_size", 20))


def arc_start(chapter_no: int, size: int) -> int:
    return ((chapter_no - 1) // size) * size + 1


def arcs(db: Session, novel_id: int, before_no: int | None = None) -> list[ArcSummary]:
    q = db.query(ArcSummary).filter(ArcSummary.novel_id == novel_id)
    if before_no is not None:
        q = q.filter(ArcSummary.end_no < before_no)
    return q.order_by(ArcSummary.start_no).all()


def arcs_text(db: Session, novel_id: int, before_no: int | None = None) -> str:
    rows = [a for a in arcs(db, novel_id, before_no) if (a.content or "").strip()]
    return "\n\n".join(f"第{a.start_no}-{a.end_no}章：{a.content.strip()}" for a in rows)


def covered_until(db: Session, novel_id: int, before_no: int) -> int:
    """分段总结覆盖到的最后一章（摘要窗口可以从它之后开始，省出空间）"""
    rows = arcs(db, novel_id, before_no)
    return max((a.end_no for a in rows if (a.content or "").strip()), default=0)


def missing_arcs(db: Session, novel_id: int) -> list[tuple[int, int]]:
    """已经写满、但还没有总结（或总结已过期）的分段"""
    size = arc_size(db)
    if size <= 0:
        return []
    from sqlalchemy import func
    max_no = int(db.query(func.max(Chapter.chapter_no)).filter(
        Chapter.novel_id == novel_id, Chapter.status.in_(CHAPTER_WRITTEN_STATUSES)).scalar() or 0)
    done = {a.start_no for a in arcs(db, novel_id) if (a.content or "").strip()}
    out = []
    start = 1
    while start + size - 1 <= max_no:
        if start not in done:
            out.append((start, start + size - 1))
        start += size
    return out


def invalidate_arc(db: Session, novel_id: int, chapter_no: int) -> None:
    """重写了某章：所在分段的总结作废（清空内容，之后自动重新生成）"""
    size = arc_size(db)
    if size <= 0:
        return
    row = db.query(ArcSummary).filter(ArcSummary.novel_id == novel_id,
                                      ArcSummary.start_no == arc_start(chapter_no, size)).first()
    if row and (row.content or "").strip():
        row.content = ""
        row.updated_at = now()
        db.flush()


def arc_source_text(db: Session, novel_id: int, start_no: int, end_no: int) -> str:
    rows = db.query(Chapter).filter(
        Chapter.novel_id == novel_id, Chapter.chapter_no >= start_no, Chapter.chapter_no <= end_no,
        Chapter.status.in_(CHAPTER_WRITTEN_STATUSES)).order_by(Chapter.chapter_no).all()
    return "\n\n".join(f"第{c.chapter_no}章 {c.title}\n{(c.summary or '（无摘要）').strip()}" for c in rows)


def save_arc(db: Session, novel_id: int, start_no: int, end_no: int, content: str) -> ArcSummary:
    row = db.query(ArcSummary).filter(ArcSummary.novel_id == novel_id, ArcSummary.start_no == start_no).first()
    if not row:
        row = ArcSummary(novel_id=novel_id, start_no=start_no, created_at=now())
        db.add(row)
    row.end_no = end_no
    row.content = content.strip()
    row.updated_at = now()
    db.flush()
    return row


# ---------- 重复套路检测 ----------

def repetition_check(db: Session, novel_id: int, chapter_no: int, summary: str,
                     window: int = 30, threshold: float = 0.55) -> list[tuple[int, float]]:
    """新章摘要与之前 window 章摘要的相似度，超过阈值的返回 [(章号, 相似度)]——用来发现"换个名字又写一遍"的桥段"""
    if not (summary or "").strip():
        return []
    rows = db.query(Chapter.chapter_no, Chapter.summary).filter(
        Chapter.novel_id == novel_id, Chapter.chapter_no < chapter_no,
        Chapter.chapter_no >= chapter_no - window - 1).all()
    x = memory_service._bigrams(summary)
    hits = []
    for no, s in rows:
        if no == chapter_no - 1 or not (s or "").strip():
            continue  # 紧挨着的上一章本来就该衔接，不算
        y = memory_service._bigrams(s)
        if not x or not y:
            continue
        score = len(x & y) / len(x | y)  # Jaccard：比重叠系数严格，长短摘要都不易误报
        if score >= threshold:
            hits.append((int(no), round(score, 2)))
    return sorted(hits, key=lambda h: -h[1])[:3]
