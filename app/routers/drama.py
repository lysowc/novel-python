"""短剧：分集方案、剧本、分镜的查看与编辑（AI 生成走 /api/admin/ai/tasks：drama_plan / drama_script / drama_storyboard）"""
from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import dt_str, fail, now, ok
from app.models import DramaEpisode, Novel
from app.services import drama

router = APIRouter(prefix="/api/admin", tags=["drama"])

EDITABLE = ("title", "summary", "hook", "cliffhanger", "script")


def f_episode(ep: DramaEpisode, changed: set[int] | None = None, full: bool = False) -> dict:
    shots = drama.load_storyboard(ep)
    data = {
        "id": ep.id, "episode_no": ep.episode_no, "title": ep.title or "",
        "chapter_from": ep.chapter_from, "chapter_to": ep.chapter_to,
        "summary": ep.summary or "", "hook": ep.hook or "", "cliffhanger": ep.cliffhanger or "",
        "has_script": bool((ep.script or "").strip()), "script_words": len((ep.script or "").strip()),
        "shot_count": len(shots), "duration": round(sum(float(s.get("duration") or 0) for s in shots), 1),
        "source_changed": bool(changed and ep.id in changed),
        "script_at": dt_str(ep.script_at), "storyboard_at": dt_str(ep.storyboard_at),
        "updated_at": dt_str(ep.updated_at),
    }
    if full:
        data["script"] = ep.script or ""
        data["storyboard"] = shots
    return data


def _novel(db: Session, nid: int) -> Novel | None:
    return db.query(Novel).filter(Novel.id == nid, Novel.deleted_at.is_(None)).first()


@router.get("/novels/{nid}/drama")
def drama_index(nid: int, db: Session = Depends(get_db)):
    if not _novel(db, nid):
        return fail("小说不存在")
    rows = drama.episodes(db, nid)
    changed = drama.source_changed(db, nid, rows)
    written = drama.written_chapters(db, nid)
    return ok({
        "episodes": [f_episode(e, changed) for e in rows],
        "covered_until": drama.covered_until(db, nid),
        "written_until": written[-1].chapter_no if written else 0,
        "episode_seconds": drama.episode_seconds(db),
    })


@router.get("/drama/episodes/{eid}")
def episode_show(eid: int, db: Session = Depends(get_db)):
    ep = db.get(DramaEpisode, eid)
    if not ep:
        return fail("分集不存在")
    return ok(f_episode(ep, drama.source_changed(db, ep.novel_id, [ep]), full=True))


@router.put("/drama/episodes/{eid}")
def episode_update(eid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    ep = db.get(DramaEpisode, eid)
    if not ep:
        return fail("分集不存在")
    for key in EDITABLE:
        if key in payload:
            value = str(payload.get(key) or "").strip()
            setattr(ep, key, value[:255] if key == "title" else value)
    if "script" in payload:
        ep.script_at = now()
    ep.updated_at = now()
    db.commit()
    return ok(f_episode(ep, full=True), "已保存")


def _renumber(db: Session, nid: int) -> None:
    """删掉中间的集后重新编号，保持 1、2、3… 连续（先挪到负数避开唯一键冲突）"""
    rows = drama.episodes(db, nid)
    for e in rows:
        e.episode_no = -e.episode_no
    db.flush()
    for i, e in enumerate(sorted(rows, key=lambda r: -r.episode_no), 1):
        e.episode_no = i
    db.flush()


@router.post("/novels/{nid}/drama/batch-delete")
def drama_batch_delete(nid: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    ids = [int(i) for i in (payload.get("ids") or []) if str(i).isdigit()]
    if not ids:
        return fail("请选择要删除的分集")
    n = db.query(DramaEpisode).filter(DramaEpisode.novel_id == nid, DramaEpisode.id.in_(ids)) \
        .delete(synchronize_session=False)
    db.flush()
    _renumber(db, nid)
    db.commit()
    return ok({"deleted": n}, f"已删除 {n} 集")


@router.post("/novels/{nid}/drama/clear")
def drama_clear(nid: int, db: Session = Depends(get_db)):
    n = db.query(DramaEpisode).filter(DramaEpisode.novel_id == nid).delete(synchronize_session=False)
    db.commit()
    return ok({"deleted": n}, f"已清空 {n} 集")
