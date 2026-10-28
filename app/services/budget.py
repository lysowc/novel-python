"""费用上限：每日总花费、每本小说每日花费超过设置值时，不再发起新的 AI 任务"""
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AiLog
from app.services.ai_client import AiFatalError, config_value


class BudgetExceeded(AiFatalError):
    """今日费用已达上限（属于"不可恢复"错误：连续续写遇到会停下）"""


def _limit(db: Session, key: str) -> float:
    try:
        return max(0.0, float(config_value(db, key, 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def today_start() -> datetime:
    # ai_log.created_at 用的是服务器本地时间（helpers.now），这里保持一致
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def spent_today(db: Session, novel_id: int | None = None) -> float:
    q = db.query(func.coalesce(func.sum(AiLog.cost), 0)).filter(AiLog.created_at >= today_start())
    if novel_id:
        q = q.filter(AiLog.novel_id == novel_id)
    return float(q.scalar() or 0)


def block_reason(db: Session, novel_id: int = 0) -> str:
    """超出上限时返回原因，否则返回空串"""
    daily = _limit(db, "budget_daily")
    if daily > 0:
        total = spent_today(db)
        if total >= daily:
            return f"今日 AI 费用已达上限（已用 ¥{total:.2f} / 上限 ¥{daily:.2f}），明天自动恢复，或到系统设置调高上限"
    per_novel = _limit(db, "budget_novel_daily")
    if per_novel > 0 and novel_id:
        used = spent_today(db, novel_id)
        if used >= per_novel:
            return f"这本小说今日 AI 费用已达上限（已用 ¥{used:.2f} / 上限 ¥{per_novel:.2f}），明天自动恢复，或到系统设置调高上限"
    return ""


def ensure(db: Session, novel_id: int = 0) -> None:
    reason = block_reason(db, novel_id)
    if reason:
        raise BudgetExceeded(reason)
