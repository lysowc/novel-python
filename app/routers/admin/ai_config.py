"""后台 API：AI Provider / 模型 / Prompt / AI 日志"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import dt_str, fail, now, ok
from app.models import (
    AiLog, AiModel, AiProvider, Prompt,
)
from app.services import prompt_service
from app.routers.admin.formatters import f_provider, f_model, f_log

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============ AI 配置 ============

@router.get("/ai/providers")
def ai_providers(db: Session = Depends(get_db)):
    rows = db.query(AiProvider).order_by(AiProvider.is_default.desc(), AiProvider.id).all()
    return ok([f_provider(p) for p in rows])


@router.post("/ai/providers")
def ai_provider_store(payload: dict, db: Session = Depends(get_db)):
    name = str(payload.get("name") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()
    if not name or not base_url:
        return fail("名称和 base_url 不能为空")
    p = AiProvider(
        name=name, base_url=base_url, api_key=str(payload.get("api_key") or "").strip(),
        status=1 if payload.get("status", 1) else 0,
        is_default=1 if db.query(AiProvider).count() == 0 else 0,
        created_at=now(), updated_at=now(),
    )
    db.add(p)
    db.commit()
    return ok({"id": p.id, "api_key": p.masked_key}, "创建成功")


@router.put("/ai/providers/{pid}")
def ai_provider_update(pid: int, payload: dict, db: Session = Depends(get_db)):
    p = db.query(AiProvider).filter(AiProvider.id == pid).first()
    if not p:
        return fail("Provider 不存在")
    if payload.get("name") is not None:
        p.name = str(payload.get("name")).strip() or p.name
    if payload.get("base_url") is not None:
        p.base_url = str(payload.get("base_url")).strip() or p.base_url
    if payload.get("api_key") is not None and str(payload.get("api_key")).strip():
        p.api_key = str(payload.get("api_key")).strip()
    if payload.get("status") is not None:
        p.status = 1 if payload.get("status") else 0
    p.updated_at = now()
    db.commit()
    return ok({"id": p.id, "api_key": p.masked_key}, "已保存")


@router.delete("/ai/providers/{pid}")
def ai_provider_destroy(pid: int, db: Session = Depends(get_db)):
    p = db.query(AiProvider).filter(AiProvider.id == pid).first()
    if not p:
        return fail("Provider 不存在")
    if db.query(AiModel).filter(AiModel.provider_id == pid).first():
        return fail("该 Provider 下还有模型，请先删除模型")
    db.delete(p)
    db.commit()
    return ok(None, "已删除")


@router.post("/ai/providers/{pid}/default")
def ai_provider_default(pid: int, db: Session = Depends(get_db)):
    p = db.query(AiProvider).filter(AiProvider.id == pid).first()
    if not p:
        return fail("Provider 不存在")
    db.query(AiProvider).update({AiProvider.is_default: 0})
    p.is_default = 1
    db.commit()
    return ok(None, "已设为默认")


@router.get("/ai/models")
def ai_models(db: Session = Depends(get_db)):
    rows = db.query(AiModel).order_by(AiModel.is_default.desc(), AiModel.id).all()
    return ok([f_model(m) for m in rows])


def _price(value) -> float:
    """模型单价（每百万 tokens），非法或负数按 0"""
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


@router.post("/ai/models")
def ai_model_store(payload: dict, db: Session = Depends(get_db)):
    provider_id = int(payload.get("provider_id") or 0)
    name = str(payload.get("name") or "").strip()
    if not provider_id or not db.query(AiProvider).filter(AiProvider.id == provider_id).first():
        return fail("请选择 Provider")
    if not name:
        return fail("模型名不能为空")
    m = AiModel(
        provider_id=provider_id, name=name,
        display_name=str(payload.get("display_name") or name).strip(),
        max_tokens=int(payload.get("max_tokens") or 0) or None,
        context_window=max(0, int(payload.get("context_window") or 0)),
        temperature=float(payload.get("temperature")) if payload.get("temperature") not in (None, "") else None,
        price_input=_price(payload.get("price_input")), price_output=_price(payload.get("price_output")),
        status=1 if payload.get("status", 1) else 0,
        is_default=1 if db.query(AiModel).count() == 0 else 0,
        created_at=now(), updated_at=now(),
    )
    db.add(m)
    db.commit()
    return ok(f_model(m), "创建成功")


@router.put("/ai/models/{mid}")
def ai_model_update(mid: int, payload: dict, db: Session = Depends(get_db)):
    m = db.query(AiModel).filter(AiModel.id == mid).first()
    if not m:
        return fail("模型不存在")
    if payload.get("provider_id") is not None:
        provider_id = int(payload.get("provider_id"))
        if not db.query(AiProvider).filter(AiProvider.id == provider_id).first():
            return fail("Provider 不存在")
        m.provider_id = provider_id
    if payload.get("name") is not None:
        m.name = str(payload.get("name")).strip() or m.name
    if payload.get("display_name") is not None:
        m.display_name = str(payload.get("display_name")).strip()
    if payload.get("max_tokens") is not None:
        m.max_tokens = int(payload.get("max_tokens")) or None
    if payload.get("context_window") is not None:
        m.context_window = max(0, int(payload.get("context_window") or 0))
    if payload.get("temperature") is not None:
        m.temperature = None if payload.get("temperature") == "" else float(payload.get("temperature"))
    if payload.get("price_input") is not None:
        m.price_input = _price(payload.get("price_input"))
    if payload.get("price_output") is not None:
        m.price_output = _price(payload.get("price_output"))
    if payload.get("status") is not None:
        m.status = 1 if payload.get("status") else 0
    m.updated_at = now()
    db.commit()
    return ok(f_model(m), "已保存")


@router.delete("/ai/models/{mid}")
def ai_model_destroy(mid: int, db: Session = Depends(get_db)):
    m = db.query(AiModel).filter(AiModel.id == mid).first()
    if not m:
        return fail("模型不存在")
    db.delete(m)
    db.commit()
    return ok(None, "已删除")


@router.post("/ai/models/{mid}/default")
def ai_model_default(mid: int, db: Session = Depends(get_db)):
    m = db.query(AiModel).filter(AiModel.id == mid).first()
    if not m:
        return fail("模型不存在")
    db.query(AiModel).update({AiModel.is_default: 0})
    m.is_default = 1
    db.commit()
    return ok(None, "已设为默认")


# ============ Prompt ============

@router.get("/prompts")
def prompts(db: Session = Depends(get_db)):
    rows = db.query(Prompt).order_by(Prompt.id).all()
    return ok([{
        "id": p.id, "type": p.type, "name": p.name, "description": p.description,
        "content": p.content, "variables": prompt_service.VARIABLES.get(p.type, []),
        "updated_at": dt_str(p.updated_at),
    } for p in rows])


@router.put("/prompts/{pid}")
def prompt_update(pid: int, payload: dict, db: Session = Depends(get_db)):
    p = db.query(Prompt).filter(Prompt.id == pid).first()
    if not p:
        return fail("Prompt 不存在")
    content = str(payload.get("content") or "")
    if not content.strip():
        return fail("Prompt 内容不能为空")
    p.content = content
    p.updated_at = now()
    db.commit()
    return ok(None, "已保存")


# ============ AI 日志 ============

@router.get("/ai/logs")
def ai_logs(page: int = Query(1), page_size: int = Query(20), db: Session = Depends(get_db)):
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    total = db.query(AiLog).count()
    rows = db.query(AiLog).order_by(AiLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return ok({"list": [f_log(l) for l in rows], "total": total, "page": page, "page_size": page_size})


@router.delete("/ai/logs")
def ai_logs_clear(db: Session = Depends(get_db)):
    db.query(AiLog).delete()
    db.commit()
    return ok(None, "日志已清空")
