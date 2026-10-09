"""全部数据库模型（与 PHP 版表结构一一对应，共用同一 MySQL 库）"""
from datetime import datetime

from sqlalchemy import (
    BigInteger, Column, DateTime, Integer, JSON, Numeric, SmallInteger, String, Text,
)
from sqlalchemy.orm import relationship

from app.db import Base


class Admin(Base):
    __tablename__ = "admin"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), nullable=False, unique=True)
    password = Column(String(255), nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class Category(Base):
    __tablename__ = "category"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False)
    description = Column(String(255), nullable=False, default="")
    sort = Column(Integer, nullable=False, default=0)
    status = Column(SmallInteger, nullable=False, default=1)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class Novel(Base):
    __tablename__ = "novel"
    id = Column(Integer, primary_key=True, autoincrement=True)
    category_id = Column(Integer, nullable=True)
    title = Column(String(255), nullable=False)
    cover = Column(String(500), nullable=False, default="")
    description = Column(Text, nullable=True)
    tags = Column(String(500), nullable=False, default="")
    status = Column(String(20), nullable=False, default="draft")
    is_public = Column(SmallInteger, nullable=False, default=0)
    word_count = Column(Integer, nullable=False, default=0)
    chapter_count = Column(Integer, nullable=False, default=0)
    outline = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    category = relationship("Category", lazy="joined",
                            primaryjoin="foreign(Novel.category_id) == Category.id")
    setting = relationship("NovelSetting", uselist=False, lazy="selectin",
                           primaryjoin="foreign(NovelSetting.novel_id) == Novel.id")
    memory = relationship("NovelMemory", uselist=False, lazy="selectin",
                          primaryjoin="foreign(NovelMemory.novel_id) == Novel.id")

    STATUS_TEXT = {"draft": "创作中", "published": "连载中", "finished": "已完结"}

    @property
    def status_text(self) -> str:
        return self.STATUS_TEXT.get(self.status, self.status)

    def get_outline_array(self) -> dict:
        import json
        data = json.loads(self.outline) if self.outline else None
        return data if isinstance(data, dict) else {"volumes": []}


class NovelSetting(Base):
    __tablename__ = "novel_setting"
    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, nullable=False, unique=True)
    world_view = Column(Text, nullable=True)
    characters = Column(Text, nullable=True)
    factions = Column(Text, nullable=True)
    conflicts = Column(Text, nullable=True)
    main_plot = Column(Text, nullable=True)
    style = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    FIELD_LABELS = {
        "world_view": "世界观",
        "characters": "主要人物设定",
        "factions": "主要势力",
        "conflicts": "核心冲突",
        "main_plot": "故事主线",
        "style": "文风要求",
    }

    def is_empty(self) -> bool:
        return all(not (getattr(self, f) or "").strip() for f in self.FIELD_LABELS)

    def to_context_text(self) -> str:
        parts = []
        for field, label in self.FIELD_LABELS.items():
            value = (getattr(self, field) or "").strip()
            if value:
                parts.append(f"【{label}】\n{value}")
        return "\n\n".join(parts)


class Chapter(Base):
    __tablename__ = "chapter"
    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, nullable=False, index=True)
    chapter_no = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    word_count = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="published")
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class Idea(Base):
    __tablename__ = "idea"
    id = Column(Integer, primary_key=True, autoincrement=True)
    category_id = Column(Integer, nullable=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="unused")
    novel_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    category = relationship("Category", lazy="joined",
                            primaryjoin="foreign(Idea.category_id) == Category.id")


class IdeaChat(Base):
    __tablename__ = "idea_chat"
    id = Column(Integer, primary_key=True, autoincrement=True)
    idea_id = Column(Integer, nullable=False, index=True)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=True)


class AiProvider(Base):
    __tablename__ = "ai_provider"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False)
    base_url = Column(String(500), nullable=False)
    api_key = Column(String(500), nullable=False, default="")
    status = Column(SmallInteger, nullable=False, default=1)
    is_default = Column(SmallInteger, nullable=False, default=0)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    @property
    def masked_key(self) -> str:
        key = self.api_key or ""
        if key == "":
            return ""
        if len(key) <= 8:
            return "****"
        return key[:3] + "****" + key[-4:]


class AiModel(Base):
    __tablename__ = "ai_model"
    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(Integer, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    display_name = Column(String(128), nullable=False, default="")
    max_tokens = Column(Integer, nullable=True)
    temperature = Column(Numeric(4, 2), nullable=True)
    status = Column(SmallInteger, nullable=False, default=1)
    is_default = Column(SmallInteger, nullable=False, default=0)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    provider = relationship("AiProvider", lazy="joined",
                            primaryjoin="foreign(AiModel.provider_id) == AiProvider.id")


class Prompt(Base):
    __tablename__ = "prompt"
    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(64), nullable=False, unique=True)
    name = Column(String(128), nullable=False)
    description = Column(String(255), nullable=False, default="")
    content = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=True)


class NovelMemory(Base):
    __tablename__ = "novel_memory"
    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, nullable=False, unique=True)
    content = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    def to_context_text(self) -> str:
        from app.services.memory import to_context_text
        return to_context_text(self.content)


class AiTask(Base):
    __tablename__ = "ai_task"
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_type = Column(String(64), nullable=False)
    ref_id = Column(Integer, nullable=False, default=0)
    ref_type = Column(String(32), nullable=False, default="novel")
    params = Column(JSON, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    error_message = Column(String(1000), nullable=False, default="")
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    TYPE_TEXT = {
        "generate_setting": "生成设定",
        "generate_outline": "生成大纲",
        "generate_chapter": "生成章节",
        "continue_chapter": "AI 续写",
        "regenerate_chapter": "重新生成",
        "generate_summary": "生成摘要",
        "update_memory": "更新记忆",
        "consistency_check": "一致性审校",
    }

    @property
    def type_text(self) -> str:
        return self.TYPE_TEXT.get(self.task_type, self.task_type)


class AiLog(Base):
    __tablename__ = "ai_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    provider = Column(String(64), nullable=False, default="")
    model = Column(String(128), nullable=False, default="")
    task_type = Column(String(64), nullable=False, default="")
    prompt = Column(Text, nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    duration = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="success")
    error_message = Column(String(1000), nullable=False, default="")
    created_at = Column(DateTime, nullable=True)


class SystemConfig(Base):
    __tablename__ = "system_config"
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(64), nullable=False, unique=True)
    value = Column(Text, nullable=True)
    description = Column(String(255), nullable=False, default="")
    updated_at = Column(DateTime, nullable=True)


class ConsistencyReport(Base):
    __tablename__ = "novel_consistency_report"
    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, nullable=False, index=True)
    chapter_no = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="warning")
    report = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=True)
