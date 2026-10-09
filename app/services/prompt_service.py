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
}


def render(db: Session, type_: str, vars_: dict | None = None) -> str:
    prompt = db.query(Prompt).filter(Prompt.type == type_).first()
    if not prompt or not (prompt.content or "").strip():
        raise RuntimeError(f"Prompt 未配置: {type_}，请到 Prompt 管理里恢复默认")
    content = prompt.content
    for key, value in (vars_ or {}).items():
        content = content.replace("{{" + key + "}}", str(value))
    return content
