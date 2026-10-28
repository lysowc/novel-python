"""长篇资料库：人物档案 / 名词表 / 分段总结 的查看与编辑"""
from fastapi import APIRouter, Body, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import dt_str, fail, now, ok
from app.models import ArcSummary, CharacterProfile, Novel, StoryTerm
from app.services import story_bible

router = APIRouter(prefix="/api/admin", tags=["story-bible"])

PROFILE_EDITABLE = ("name", "aliases", "role", "appearance", "personality", "abilities", "realm",
                    "relationships", "status", "notes")
LIMITS = {"name": 64, "aliases": 255, "role": 32, "realm": 128, "status": 255}


def f_profile(p: CharacterProfile) -> dict:
    return {
        "id": p.id, "name": p.name, "aliases": p.aliases or "", "role": p.role or "",
        "appearance": p.appearance or "", "personality": p.personality or "", "abilities": p.abilities or "",
        "realm": p.realm or "", "realm_history": p.realm_history or [], "relationships": p.relationships or "",
        "status": p.status or "", "notes": p.notes or "", "first_chapter": p.first_chapter or 0,
        "last_chapter": p.last_chapter or 0, "locked": int(p.locked or 0), "updated_at": dt_str(p.updated_at),
    }


def f_term(t: StoryTerm) -> dict:
    return {"id": t.id, "term": t.term, "type": t.type or "", "description": t.description or "",
            "first_chapter": t.first_chapter or 0, "locked": int(t.locked or 0), "updated_at": dt_str(t.updated_at)}


def f_arc(a: ArcSummary) -> dict:
    return {"id": a.id, "start_no": a.start_no, "end_no": a.end_no, "content": a.content or "",
            "updated_at": dt_str(a.updated_at)}


def _novel(db: Session, nid: int) -> Novel | None:
    return db.query(Novel).filter(Novel.id == nid, Novel.deleted_at.is_(None)).first()


@router.get("/novels/{nid}/bible")
def bible_index(nid: int, db: Session = Depends(get_db)):
    if not _novel(db, nid):
        return fail("小说不存在")
    return ok({
        "profiles": [f_profile(p) for p in story_bible.profiles(db, nid)],
        "terms": [f_term(t) for t in story_bible.terms(db, nid)],
        "arcs": [f_arc(a) for a in story_bible.arcs(db, nid)],
        "arc_size": story_bible.arc_size(db),
        "missing_arcs": [{"start_no": a, "end_no": b} for a, b in story_bible.missing_arcs(db, nid)],
    })


def _apply_profile(p: CharacterProfile, payload: dict) -> str | None:
    for key in PROFILE_EDITABLE:
        if key in payload:
            value = str(payload.get(key) or "").strip()
            if key in LIMITS:
                value = value[:LIMITS[key]]
            if key == "realm" and value != (p.realm or ""):
                # 手动改境界也记一笔，章号用最近出场章
                history = list(p.realm_history or [])
                history.append({"chapter": int(p.last_chapter or 0), "realm": value})
                p.realm_history = history[-30:]
            setattr(p, key, value)
    if "realm_history" in payload and isinstance(payload["realm_history"], list):
        p.realm_history = [
            {"chapter": int(h.get("chapter") or 0), "realm": str(h.get("realm") or "").strip()[:128]}
            for h in payload["realm_history"] if isinstance(h, dict) and str(h.get("realm") or "").strip()
        ][-30:]
    if "locked" in payload:
        p.locked = 1 if payload["locked"] else 0
    if not (p.name or "").strip():
        return "姓名不能为空"
    p.updated_at = now()
    return None


@router.post("/novels/{nid}/profiles")
def profile_create(nid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    if not _novel(db, nid):
        return fail("小说不存在")
    # 手动建的档案默认锁定：AI 不会改动作者写的人设
    p = CharacterProfile(novel_id=nid, aliases="", role="", realm="", status="", realm_history=[],
                         first_chapter=int(payload.get("first_chapter") or 0), last_chapter=0,
                         locked=1, created_at=now())
    err = _apply_profile(p, {"locked": 1, **payload})
    if err:
        return fail(err)
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return fail("已有同名人物档案")
    return ok(f_profile(p))


@router.put("/profiles/{pid}")
def profile_update(pid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    p = db.query(CharacterProfile).filter(CharacterProfile.id == pid).first()
    if not p:
        return fail("档案不存在")
    err = _apply_profile(p, payload)
    if err:
        return fail(err)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return fail("已有同名人物档案")
    return ok(f_profile(p))


@router.delete("/profiles/{pid}")
def profile_delete(pid: int, db: Session = Depends(get_db)):
    p = db.query(CharacterProfile).filter(CharacterProfile.id == pid).first()
    if not p:
        return fail("档案不存在")
    db.delete(p)
    db.commit()
    return ok()


def _apply_term(t: StoryTerm, payload: dict) -> str | None:
    if "term" in payload:
        t.term = str(payload.get("term") or "").strip()[:64]
    if "type" in payload:
        t.type = str(payload.get("type") or "").strip()[:32]
    if "description" in payload:
        t.description = str(payload.get("description") or "").strip()[:500]
    if "locked" in payload:
        t.locked = 1 if payload["locked"] else 0
    if not (t.term or "").strip():
        return "名词不能为空"
    t.updated_at = now()
    return None


@router.post("/novels/{nid}/terms")
def term_create(nid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    if not _novel(db, nid):
        return fail("小说不存在")
    t = StoryTerm(novel_id=nid, type="", first_chapter=int(payload.get("first_chapter") or 0), locked=1,
                  created_at=now())
    err = _apply_term(t, {"locked": 1, **payload})
    if err:
        return fail(err)
    db.add(t)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return fail("名词已存在")
    return ok(f_term(t))


@router.put("/terms/{tid}")
def term_update(tid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    t = db.query(StoryTerm).filter(StoryTerm.id == tid).first()
    if not t:
        return fail("名词不存在")
    err = _apply_term(t, payload)
    if err:
        return fail(err)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return fail("名词已存在")
    return ok(f_term(t))


@router.delete("/terms/{tid}")
def term_delete(tid: int, db: Session = Depends(get_db)):
    t = db.query(StoryTerm).filter(StoryTerm.id == tid).first()
    if not t:
        return fail("名词不存在")
    db.delete(t)
    db.commit()
    return ok()


@router.put("/arcs/{aid}")
def arc_update(aid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    a = db.query(ArcSummary).filter(ArcSummary.id == aid).first()
    if not a:
        return fail("分段总结不存在")
    a.content = str(payload.get("content") or "").strip()
    a.updated_at = now()
    db.commit()
    return ok(f_arc(a))
