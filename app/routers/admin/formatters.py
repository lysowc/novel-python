"""后台 API：后台接口的数据格式化（时间统一 Y-m-d H:i:s）"""


from app.helpers import dt_str
from app.models import (
    AiLog, AiModel, AiProvider, AiTask, Category, Chapter, Idea, Novel,
)



# ============ 格式化工（时间统一 Y-m-d H:i:s） ============

def f_category(c: Category, novel_count: int = 0) -> dict:
    return {
        "id": c.id, "name": c.name, "description": c.description, "sort": c.sort,
        "status": c.status, "novel_count": novel_count,
        "created_at": dt_str(c.created_at), "updated_at": dt_str(c.updated_at),
    }


def f_novel(n: Novel, with_public: bool = True) -> dict:
    data = {
        "id": n.id, "category_id": n.category_id,
        "category_name": n.category.name if n.category else "",
        "title": n.title, "cover": n.cover, "description": n.description,
        "tags": n.tags, "status": n.status, "status_text": n.status_text,
        "word_count": n.word_count, "chapter_count": n.chapter_count,
        "created_at": dt_str(n.created_at), "updated_at": dt_str(n.updated_at),
    }
    if with_public:
        data["is_public"] = n.is_public
    return data


def f_chapter(c: Chapter) -> dict:
    return {
        "id": c.id, "novel_id": c.novel_id, "chapter_no": c.chapter_no,
        "title": c.title, "summary": c.summary, "word_count": c.word_count,
        "status": c.status, "created_at": dt_str(c.created_at), "updated_at": dt_str(c.updated_at),
    }


def f_chapter_full(c: Chapter) -> dict:
    data = f_chapter(c)
    data["content"] = c.content
    return data


def f_idea(i: Idea) -> dict:
    return {
        "id": i.id, "category_id": i.category_id,
        "category_name": i.category.name if i.category else "",
        "title": i.title, "content": i.content, "status": i.status,
        "novel_id": i.novel_id, "created_at": dt_str(i.created_at), "updated_at": dt_str(i.updated_at),
    }


def f_provider(p: AiProvider) -> dict:
    return {
        "id": p.id, "name": p.name, "base_url": p.base_url, "api_key": p.masked_key,
        "status": p.status, "is_default": p.is_default,
        "created_at": dt_str(p.created_at), "updated_at": dt_str(p.updated_at),
    }


def f_model(m: AiModel) -> dict:
    return {
        "id": m.id, "provider_id": m.provider_id,
        "provider_name": m.provider.name if m.provider else "",
        "name": m.name, "display_name": m.display_name,
        "max_tokens": m.max_tokens, "context_window": int(m.context_window or 0),
        "temperature": float(m.temperature) if m.temperature is not None else None,
        "price_input": float(m.price_input or 0), "price_output": float(m.price_output or 0),
        "status": m.status, "is_default": m.is_default,
        "created_at": dt_str(m.created_at), "updated_at": dt_str(m.updated_at),
    }


def f_task(t: AiTask) -> dict:
    return {
        "id": t.id, "task_type": t.task_type, "task_type_text": t.type_text,
        "ref_id": t.ref_id, "ref_type": t.ref_type, "params": t.params,
        "status": t.status, "error_message": t.error_message,
        "created_at": dt_str(t.created_at), "updated_at": dt_str(t.updated_at),
    }


def f_log(l: AiLog) -> dict:
    return {
        "id": l.id, "novel_id": l.novel_id, "provider": l.provider, "model": l.model, "task_type": l.task_type,
        "cost": float(l.cost or 0), "usage_estimated": l.usage_estimated,
        "prompt": l.prompt, "prompt_tokens": l.prompt_tokens,
        "completion_tokens": l.completion_tokens, "total_tokens": l.total_tokens,
        "duration": l.duration, "status": l.status, "error_message": l.error_message,
        "created_at": dt_str(l.created_at),
    }
