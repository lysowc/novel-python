"""AI 创作业务编排：设定 / 大纲 / 章节正文 / 摘要 / 记忆 / 一致性审校

对 PHP 版 AiService 的完整移植，含三大升级：
- 检索式上下文（生成前按相关性召回历史章节）
- 结构化记忆 v2
- 一致性审校 + 每 N 章自动触发
"""
import json
import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.helpers import now, word_count
from app.models import (
    Chapter, ConsistencyReport, Idea, Novel, NovelMemory, NovelSetting, SystemConfig,
)
from app.services import context_builder, memory as memory_service
from app.services import prompt_service, retrieval, task_service
from app.services.ai_client import AiClient, config_value


def parse_sections(text: str) -> dict:
    parts = re.split(r"【(.+?)】", text)
    if len(parts) < 2:
        return {}
    sections = {}
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = (parts[i + 1] if i + 1 < len(parts) else "").strip()
        sections[title] = body
    return sections


def extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class AiService:
    def __init__(self, db: Session):
        self.db = db
        self.client = AiClient(db)

    # ============ 设定 ============

    def generate_setting(self, novel: Novel, on_stage=None) -> None:
        on_stage and on_stage("request", "AI 正在生成小说设定...")
        idea = self.db.query(Idea).filter(Idea.novel_id == novel.id).first()
        idea_content = (idea.content if idea and (idea.content or "").strip()
                        else (novel.description or "").strip())
        system = prompt_service.render(self.db, "novel_setting", {"idea_content": idea_content})
        result = self.client.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": "请根据以上要求，输出完整的小说设定。"}],
            {"task_type": "generate_setting", "max_tokens": 8192},
        )
        sections = parse_sections(result["text"])
        field_map = {
            "小说简介": "description",
            "世界观": "world_view",
            "主要人物设定": "characters",
            "主要势力": "factions",
            "核心冲突": "conflicts",
            "故事主线": "main_plot",
            "文风要求": "style",
        }
        setting = self.db.query(NovelSetting).filter(NovelSetting.novel_id == novel.id).first()
        if not setting:
            setting = NovelSetting(novel_id=novel.id)
            self.db.add(setting)
        for title, field in field_map.items():
            value = (sections.get(title) or "").strip()
            if field == "description":
                if value:
                    novel.description = value
            elif value:
                setattr(setting, field, value)
        self.db.commit()
        on_stage and on_stage("done", "设定生成完成")

    # ============ 大纲 ============

    def generate_outline(self, novel: Novel, on_stage=None) -> None:
        on_stage and on_stage("request", "AI 正在生成章节大纲...")
        volumes = int(float(config_value(self.db, "outline_volumes", 3)))
        chapters = int(float(config_value(self.db, "outline_chapters_per_volume", 20)))
        system = prompt_service.render(self.db, "outline", {"volumes": volumes, "chapters": chapters})
        timeout = int(float(config_value(self.db, "ai_http_timeout", 120))) * 3
        result = self.client.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": "【小说设定】\n" + context_builder.setting_text(novel)}],
            {"task_type": "generate_outline", "max_tokens": 16384, "timeout": timeout},
        )
        data = extract_json(result["text"])
        if not isinstance(data.get("volumes"), list):
            raise RuntimeError("大纲解析失败：AI 未返回合法 JSON")
        no = 1
        volumes_out = []
        for volume in data["volumes"]:
            if not isinstance(volume, dict):
                continue
            vol = {"title": str(volume.get("title", "")), "chapters": []}
            for chapter in volume.get("chapters") or []:
                if not isinstance(chapter, dict):
                    continue
                vol["chapters"].append({
                    "no": no,
                    "title": str(chapter.get("title", f"第{no}章")),
                    "summary": str(chapter.get("summary", "")),
                })
                no += 1
            volumes_out.append(vol)
        novel.outline = json.dumps({"volumes": volumes_out}, ensure_ascii=False)
        self.db.commit()
        on_stage and on_stage("done", f"大纲生成完成，共 {no - 1} 章")

    # ============ 章节 ============

    def generate_chapter(self, novel: Novel, task, on_delta=None, on_stage=None) -> Chapter:
        params = task.params or {}
        type_ = task.task_type

        if type_ in ("generate_chapter", "regenerate_chapter"):
            chapter_no = int(params.get("chapter_no") or 0)
            if chapter_no <= 0:
                raise RuntimeError("缺少 chapter_no 参数")
            exists = bool(
                self.db.query(Chapter).filter(
                    Chapter.novel_id == novel.id, Chapter.chapter_no == chapter_no).first()
            )
            if type_ == "regenerate_chapter" and not exists:
                raise RuntimeError(f"第{chapter_no}章不存在")
        else:
            max_no = (
                self.db.query(func.max(Chapter.chapter_no))
                .filter(Chapter.novel_id == novel.id)
                .scalar()
            )
            chapter_no = int(max_no or 0) + 1

        target_words = int(params.get("target_words") or 0) or int(
            float(config_value(self.db, "chapter_target_words", 3000)))
        instruction = str(params.get("instruction") or "").strip()
        max_tokens = max(1024, min(16384, target_words * 2))

        # 1. 检索相关历史章节（可召回记忆：按相关性而非按最近）
        retrieved_list = []
        if retrieval.get_config_int(self.db, "retrieval_enabled", 1) > 0:
            on_stage and on_stage("retrieval", "正在检索相关历史章节...")
            retrieved_list = retrieval.related_chapters(self.db, novel, chapter_no, instruction)
            on_stage and on_stage(
                "retrieval_done",
                f"已从历史中召回 {len(retrieved_list)} 个相关章节" if retrieved_list
                else "未找到强相关历史章节，使用常规上下文",
            )

        # 2. 生成正文（流式）
        on_stage and on_stage("content", f"开始生成第{chapter_no}章（目标 {target_words} 字）...")
        prompt_type = "chapter_continue" if type_ == "continue_chapter" else "chapter_generate"
        system = prompt_service.render(self.db, prompt_type, {"target_words": target_words})
        user_context = context_builder.build_chapter_context(
            self.db, novel, chapter_no, instruction, retrieved_list)

        result = self.client.chat_stream(
            [{"role": "system", "content": system},
             {"role": "user", "content": user_context}],
            {"task_type": type_, "max_tokens": max_tokens},
            on_delta,
        )
        content = result["text"].strip()
        if not content:
            raise RuntimeError("AI 返回内容为空")

        on_stage and on_stage("content_done", f"正文生成完成（{word_count(content)} 字），正在保存...")

        # 3. 保存章节（先成功后保存，覆盖式）
        chapter = (
            self.db.query(Chapter)
            .filter(Chapter.novel_id == novel.id, Chapter.chapter_no == chapter_no)
            .first()
        )
        if not chapter:
            chapter = Chapter(novel_id=novel.id, chapter_no=chapter_no)
            self.db.add(chapter)
        chapter.title = context_builder.outline_title(novel, chapter_no)
        chapter.content = content
        chapter.word_count = word_count(content)
        chapter.status = "published"
        chapter.updated_at = now()
        self.db.commit()
        self._recount(novel)

        # 4. 生成摘要
        on_stage and on_stage("summary", "正文已保存，正在生成章节摘要...")
        try:
            summary = self.generate_summary(novel, chapter)
            chapter.summary = summary
            self.db.commit()
            on_stage and on_stage("summary_done", "摘要生成完成")
        except Exception as e:
            raise RuntimeError(f"章节已保存，但摘要生成失败：{e}") from e

        # 5. 更新小说记忆
        on_stage and on_stage("memory", "正在更新小说记忆...")
        try:
            self.update_memory(novel)
            on_stage and on_stage("memory_done", "小说记忆已更新")
        except Exception as e:
            raise RuntimeError(f"章节已保存，但记忆更新失败：{e}") from e

        # 6. 自动一致性审校（每 N 章）
        interval = int(float(config_value(self.db, "consistency_auto_interval", 0)))
        if interval > 0 and chapter_no % interval == 0:
            try:
                task_service.enqueue(self.db, "consistency_check", novel.id, {"chapter_no": chapter_no})
                on_stage and on_stage("consistency_queued", f"已自动安排第{chapter_no}章后的一致性审校")
            except Exception as e:
                on_stage and on_stage("consistency_queued", "自动审校入队失败：" + str(e))

        # 连续生成续链逻辑已移至 task_service.execute（无论成败都继续下一章，跳过失败章）
        return chapter

    def generate_summary_for_chapter(self, novel: Novel, task, on_stage=None) -> None:
        chapter_no = int(task.params.get("chapter_no") or 0) if task.params else 0
        chapter = (
            self.db.query(Chapter)
            .filter(Chapter.novel_id == novel.id, Chapter.chapter_no == chapter_no)
            .first()
        )
        if not chapter:
            raise RuntimeError(f"第{chapter_no}章不存在")
        on_stage and on_stage("request", f"正在为第{chapter_no}章生成摘要...")
        chapter.summary = self.generate_summary(novel, chapter)
        chapter.updated_at = now()
        self.db.commit()
        on_stage and on_stage("done", "摘要生成完成")

    def generate_summary(self, novel: Novel, chapter: Chapter) -> str:
        system = prompt_service.render(self.db, "chapter_summary")
        context = (
            context_builder.setting_text(novel)
            + "\n\n【小说记忆（当前状态）】\n" + context_builder.memory_text(novel)
            + f"\n\n【本章标题】{chapter.title}"
            + f"\n\n【本章正文】\n{chapter.content}"
        )
        result = self.client.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": context}],
            {"task_type": "generate_summary"},
        )
        summary = result["text"].strip()
        if not summary:
            raise RuntimeError("摘要生成结果为空")
        return summary

    # ============ 记忆 ============

    def update_memory(self, novel: Novel) -> None:
        last = (
            self.db.query(Chapter)
            .filter(Chapter.novel_id == novel.id, Chapter.status == "published")
            .order_by(Chapter.chapter_no.desc())
            .first()
        )
        if not last:
            raise RuntimeError("还没有已发布的章节，无法更新记忆")

        old_memory = self.db.query(NovelMemory).filter(NovelMemory.novel_id == novel.id).first()
        old_text = (
            memory_service.to_context_text(old_memory.content) if old_memory and (old_memory.content or "").strip()
            else "（暂无旧记忆，这是第一次建立记忆）"
        )

        chapter_content = last.content or ""
        if len(chapter_content) > 6000:
            chapter_content = chapter_content[:6000] + "\n……（正文截断）"

        system = prompt_service.render(self.db, "memory_update")
        user = (
            f"【旧记忆】\n{old_text}\n\n"
            f"【最新一章】\n第{last.chapter_no}章 {last.title}\n"
            f"本章摘要：{last.summary}\n\n"
            f"本章正文：\n{chapter_content}"
        )
        result = self.client.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            {"task_type": "update_memory"},
        )
        data = extract_json(result["text"])
        if not data:
            raise RuntimeError("记忆更新失败：AI 未返回合法 JSON")
        data = memory_service.normalize_memory(data)

        memory_row = old_memory or NovelMemory(novel_id=novel.id)
        memory_row.content = json.dumps(data, ensure_ascii=False, indent=2)
        memory_row.updated_at = now()
        if not old_memory:
            self.db.add(memory_row)
        self.db.commit()

    # ============ 一致性审校 ============

    def check_consistency(self, novel: Novel, task, on_stage=None) -> None:
        on_stage and on_stage("request", "AI 正在审校大纲与正文的一致性...")
        last = (
            self.db.query(Chapter)
            .filter(Chapter.novel_id == novel.id, Chapter.status == "published")
            .order_by(Chapter.chapter_no.desc())
            .first()
        )
        if not last:
            raise RuntimeError("还没有已发布的章节，无法审校")

        system = prompt_service.render(self.db, "consistency_check")
        user = (
            "【小说大纲】\n" + context_builder.outline_text(novel)
            + "\n\n【已写章节进度】\n" + context_builder.written_progress_text(self.db, novel)
            + "\n\n【小说记忆（当前状态）】\n" + context_builder.memory_text(novel)
        )
        result = self.client.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            {"task_type": "consistency_check", "max_tokens": 4096},
        )
        data = extract_json(result["text"])
        report = normalize_report(data)
        if report is None:
            raise RuntimeError("审校失败：AI 未返回合法报告")

        row = ConsistencyReport(
            novel_id=novel.id,
            chapter_no=int(last.chapter_no),
            status=report["status"],
            report=report,
            created_at=now(),
        )
        self.db.add(row)
        self.db.commit()

        count = len(report["issues"])
        on_stage and on_stage(
            "done",
            f"审校完成（{report['status']}）：发现 {count} 个问题" if count else "审校完成：未发现明显问题",
        )

    # ============ 工具 ============

    def _recount(self, novel: Novel) -> None:
        novel.word_count = int(
            self.db.query(func.sum(Chapter.word_count))
            .filter(Chapter.novel_id == novel.id).scalar() or 0
        )
        novel.chapter_count = (
            self.db.query(Chapter).filter(Chapter.novel_id == novel.id).count()
        )
        self.db.commit()


def normalize_report(data: dict) -> dict | None:
    """规整一致性审校报告；输入为空（AI 未返回 JSON）时返回 None"""
    if not data:
        return None
    status = str(data.get("status", ""))
    if status not in ("ok", "warning", "critical"):
        status = "warning"
    issues = []
    for issue in data.get("issues", []) if isinstance(data.get("issues"), list) else []:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity", "minor"))
        if severity not in ("minor", "major", "critical"):
            severity = "minor"
        desc = str(issue.get("description", "")).strip()
        if not desc:
            continue
        related = [int(n) for n in (issue.get("related_chapters") or []) if int(n) > 0]
        issues.append({
            "severity": severity,
            "type": str(issue.get("type", "other")).strip(),
            "description": desc,
            "suggestion": str(issue.get("suggestion", "")).strip(),
            "related_chapters": related,
        })
    if not issues and status != "ok":
        status = "ok"
    return {
        "status": status,
        "summary": str(data.get("summary", "")).strip(),
        "issues": issues,
    }
