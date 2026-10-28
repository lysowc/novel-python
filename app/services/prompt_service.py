"""Prompt 读取与渲染（{{变量}} 替换）"""
from sqlalchemy.orm import Session

from app.models import Prompt

VARIABLES = {
    "idea_chat": [],
    "novel_setting": ["{{idea_content}} 点子内容"],
    "outline": ["{{volumes}} 卷数", "{{chapters}} 每卷章数"],
    "chapter_generate": ["{{target_words}} 目标字数"],
    "chapter_continue": ["{{target_words}} 目标字数"],
    "chapter_summary": [],
    "memory_update": [],
    "consistency_check": [],
    "outline_extend": ["{{chapters}} 续写章数", "{{start_no}} 起始章号"],
}


def render(db: Session, type_: str, vars_: dict | None = None) -> str:
    prompt = db.query(Prompt).filter(Prompt.type == type_).first()
    if not prompt:
        prompt = _create_default(db, type_)
    if not prompt or not (prompt.content or "").strip():
        raise RuntimeError(f"Prompt 未配置: {type_}，请到 Prompt 管理里恢复默认")
    content = prompt.content
    for key, value in (vars_ or {}).items():
        content = content.replace("{{" + key + "}}", str(value))
    return content


def _create_default(db: Session, type_: str) -> Prompt | None:
    """新版本新增的 Prompt 类型在旧库里还没有：按默认内容自动补上（之后可在 Prompt 管理里修改）"""
    from app.helpers import now
    from app.services.prompt_defaults import PROMPT_DEFAULTS
    item = next((p for p in PROMPT_DEFAULTS if p["type"] == type_), None)
    if not item:
        return None
    prompt = Prompt(type=type_, name=item["name"], description=item["description"],
                    content=item["content"].strip(), updated_at=now())
    db.add(prompt)
    db.commit()
    return prompt
