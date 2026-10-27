"""用量与费用统计（基于 ai_log）"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import ok
from app.models import AiLog, AiTask, Novel

router = APIRouter(prefix="/api/admin/stats", tags=["stats"])

TASK_TYPE_TEXT = dict(AiTask.TYPE_TEXT, idea_chat="点子聊天", chat="其他")


def _agg():
    return (
        func.count(AiLog.id).label("calls"),
        func.coalesce(func.sum(AiLog.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(AiLog.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(AiLog.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(AiLog.cost), 0).label("cost"),
        func.coalesce(func.sum(case((AiLog.status != "success", 1), else_=0)), 0).label("failed"),
        func.coalesce(func.sum(AiLog.usage_estimated), 0).label("estimated"),
    )


def _row(r) -> dict:
    return {
        "calls": int(r.calls or 0),
        "prompt_tokens": int(r.prompt_tokens or 0),
        "completion_tokens": int(r.completion_tokens or 0),
        "total_tokens": int(r.total_tokens or 0),
        "cost": round(float(r.cost or 0), 4),
        "failed": int(r.failed or 0),
        "estimated": int(r.estimated or 0),
    }


EMPTY = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
         "cost": 0.0, "failed": 0, "estimated": 0}


@router.get("/usage")
def usage(days: int = Query(30), novel_id: int = Query(0), db: Session = Depends(get_db)):
    days = min(366, max(1, days))
    since = (datetime.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    filters = [AiLog.created_at >= since]
    if novel_id > 0:
        filters.append(AiLog.novel_id == novel_id)

    def grouped(*cols):
        return db.query(*cols, *_agg()).filter(*filters).group_by(*cols)

    totals = _row(db.query(*_agg()).filter(*filters).one())

    day_col = func.date(AiLog.created_at)
    by_day_rows = {str(r[0]): _row(r) for r in grouped(day_col).all()}
    by_day = []
    for i in range(days):
        d = (since + timedelta(days=i)).strftime("%Y-%m-%d")
        by_day.append({"date": d, **by_day_rows.get(d, EMPTY)})

    by_task = [
        {"task_type": r[0], "task_type_text": TASK_TYPE_TEXT.get(r[0], r[0]), **_row(r)}
        for r in grouped(AiLog.task_type).all()
    ]
    by_model = [{"model": r[0], **_row(r)} for r in grouped(AiLog.model).all()]

    novel_rows = grouped(AiLog.novel_id).all()
    titles = {n.id: n.title for n in db.query(Novel.id, Novel.title)
              .filter(Novel.id.in_([r[0] for r in novel_rows if r[0]])).all()} if novel_rows else {}
    by_novel = [
        {"novel_id": r[0], "title": titles.get(r[0]) or ("（点子聊天/未归属）" if not r[0] else f"已删除小说 #{r[0]}"),
         **_row(r)}
        for r in novel_rows
    ]
    for lst in (by_task, by_model, by_novel):
        lst.sort(key=lambda x: (-x["cost"], -x["total_tokens"]))

    return ok({
        "days": days, "since": since.strftime("%Y-%m-%d"),
        "totals": totals, "by_day": by_day,
        "by_task_type": by_task, "by_model": by_model, "by_novel": by_novel,
    })
