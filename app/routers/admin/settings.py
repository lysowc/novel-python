"""后台 API：系统配置"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import fail, now, ok
from app.models import (
    SystemConfig,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============ 系统配置 ============

CONFIG_KEYS = {
    "site_name": "站点名称",
    "ai_temperature": "AI 默认温度",
    "ai_http_timeout": "AI HTTP 超时（秒）",
    "context_max_recent_chapters": "上下文最近章节数",
    "context_summary_max_chars": "上下文摘要字符上限",
    "chapter_target_words": "每章目标字数",
    "outline_volumes": "大纲默认卷数",
    "outline_chapters_per_volume": "大纲每卷章数",
    "retrieval_enabled": "启用相关章节检索",
    "retrieval_max_chapters": "每章召回的相关章节数",
    "consistency_auto_interval": "自动审校间隔（章）",
    "chapter_auto_publish": "AI 新章节自动发布",
    "context_max_chars": "生成上下文总字符上限",
}

CONFIG_DEFAULTS = {
    "site_name": "AI 小说工坊", "ai_temperature": "0.8", "ai_http_timeout": "120",
    "context_max_recent_chapters": "5", "context_summary_max_chars": "12000",
    "chapter_target_words": "3000", "outline_volumes": "3",
    "outline_chapters_per_volume": "20", "retrieval_enabled": "1",
    "retrieval_max_chapters": "5", "consistency_auto_interval": "0",
    "chapter_auto_publish": "1", "context_max_chars": "60000",
}


@router.get("/config")
def config_index(db: Session = Depends(get_db)):
    rows = {c.key: c.value for c in db.query(SystemConfig).all()}
    return ok({key: rows.get(key, CONFIG_DEFAULTS[key]) for key in CONFIG_KEYS})


@router.put("/config")
def config_update(payload: dict, db: Session = Depends(get_db)):
    values = {key: payload[key] for key in CONFIG_KEYS if key in payload}
    if not values:
        return fail("没有可保存的配置")
    existing = {c.key: c for c in db.query(SystemConfig).all()}
    for key, value in values.items():
        if key in existing:
            existing[key].value = str(value)
            existing[key].updated_at = now()
        else:
            db.add(SystemConfig(key=key, value=str(value), description=CONFIG_KEYS[key], updated_at=now()))
    db.commit()
    return ok(None, "配置已保存")
