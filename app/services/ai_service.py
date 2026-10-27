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
    CHAPTER_WRITTEN_STATUSES, Chapter, ConsistencyReport, Idea, Novel, NovelMemory, NovelSetting, SystemConfig,
)
from app.services import context_builder, memory as memory_service
from app.services import chapter_versions, memory_store, prompt_service, retrieval, task_service
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

        # 重试"章节已保存、但摘要/记忆失败"的任务：不再重新生成正文，只补做后续步骤
        resume = params.get("resume") if isinstance(params.get("resume"), dict) else None
        if resume:
            return self._resume_chapter(novel, task, resume, on_stage)

        if type_ == "regenerate_chapter":
            chapter_no = int(params.get("chapter_no") or 0)
            if chapter_no <= 0:
                raise RuntimeError("缺少 chapter_no 参数")
            exists = bool(
                self.db.query(Chapter).filter(
                    Chapter.novel_id == novel.id, Chapter.chapter_no == chapter_no).first()
            )
            if not exists:
                raise RuntimeError(f"第{chapter_no}章不存在")
        else:
            # continue_chapter：永远在最大章号之后续写（忽略传入的章号，防止覆盖已有章节）
            # generate_chapter：可指定章号，未指定时默认下一章；但不允许覆盖已有章节（覆盖请用重新生成）
            chapter_no = int(params.get("chapter_no") or 0) if type_ == "generate_chapter" else 0
            if chapter_no <= 0:
                max_no = (
                    self.db.query(func.max(Chapter.chapter_no))
                    .filter(Chapter.novel_id == novel.id)
                    .scalar()
                )
                chapter_no = int(max_no or 0) + 1
            elif self.db.query(Chapter).filter(
                    Chapter.novel_id == novel.id, Chapter.chapter_no == chapter_no).first():
                raise RuntimeError(f"第{chapter_no}章已存在，如需重写请使用「重新生成」")

        target_words = int(params.get("target_words") or 0) or int(
            float(config_value(self.db, "chapter_target_words", 3000)))
        instruction = str(params.get("instruction") or "").strip()
        max_tokens = max(1024, min(16384, target_words * 2))

        # 0. 本章开始前的记忆（重写中间章节时用当时的快照，而不是全书最新记忆）
        mem_base = memory_store.memory_before(self.db, novel, chapter_no)
        mem_text = mem_base.context_text()
        if mem_base.source == memory_store.SNAPSHOT:
            on_stage and on_stage("memory_base", f"使用第{mem_base.snapshot_no}章结束时的记忆快照")
        elif mem_base.source == memory_store.CURRENT_STALE:
            on_stage and on_stage("memory_base", "没有本章之前的记忆快照，使用全书最新记忆（可能含后续剧情）")

        # 1. 检索相关历史章节（可召回记忆：按相关性而非按最近）
        retrieved_list = []
        if retrieval.get_config_int(self.db, "retrieval_enabled", 1) > 0:
            on_stage and on_stage("retrieval", "正在检索相关历史章节...")
            retrieved_list = retrieval.related_chapters(self.db, novel, chapter_no, instruction, mem_text)
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
            self.db, novel, chapter_no, instruction, retrieved_list, mem_text)

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
            # 开启「AI 章节需审阅」时新章节先存为草稿，读者看不到，审阅后再发布
            auto_publish = int(float(config_value(self.db, "chapter_auto_publish", 1))) > 0
            chapter = Chapter(novel_id=novel.id, chapter_no=chapter_no, created_at=now(),
                              status="published" if auto_publish else "draft")
            self.db.add(chapter)
        else:
            chapter_versions.save_version(self.db, chapter, "regenerate")  # 旧版本可在「历史版本」里恢复
        chapter.title = context_builder.outline_title(novel, chapter_no)
        chapter.content = content
        chapter.summary = ""  # 旧摘要对应旧正文，清空，由下一步重新生成
        chapter.word_count = word_count(content)
        chapter.updated_at = now()  # 重新生成保留原状态（已发布的不会因重写而对读者消失）
        # 记下断点：后续步骤失败时，重试只补做摘要/记忆，不会重新生成或多写一章
        task.params = {**params, "resume": {"chapter_no": chapter_no, "memory_base": mem_base.source}}
        self.db.commit()
        self._recount(novel)

        self._finish_chapter(novel, chapter, mem_base, on_stage)
        return chapter

    def _resume_chapter(self, novel: Novel, task, resume: dict, on_stage=None) -> Chapter:
        chapter_no = int(resume.get("chapter_no") or 0)
        chapter = (
            self.db.query(Chapter)
            .filter(Chapter.novel_id == novel.id, Chapter.chapter_no == chapter_no)
            .first()
        )
        if not chapter or not (chapter.content or "").strip():
            # 章节已被删除：断点作废，避免任务一直挂在"可重试"里
            task.params = {k: v for k, v in (task.params or {}).items() if k != "resume"}
            self.db.commit()
            raise RuntimeError(f"第{chapter_no}章已不存在，无需重试")
        on_stage and on_stage("resume", f"第{chapter_no}章正文已保存，跳过生成，补做摘要与记忆...")
        # 保存时是"写新章节"（基于当时的最新记忆），且记忆还没更新过 → 仍以最新记忆为基准
        if resume.get("memory_base") == memory_store.CURRENT and \
                not memory_store.get_snapshot(self.db, novel.id, chapter_no):
            mem_base = memory_store.MemoryBase(memory_store.current_content(self.db, novel.id), memory_store.CURRENT)
        else:
            mem_base = memory_store.memory_before(self.db, novel, chapter_no)
        self._finish_chapter(novel, chapter, mem_base, on_stage)
        return chapter

    def _retry_once(self, fn):
        """摘要/记忆这类后处理偶发失败（超时、JSON 不合法）时自动再试一次；配置/鉴权错误不重试"""
        from app.services.ai_client import CallbackAbort, is_fatal
        try:
            return fn()
        except CallbackAbort:
            raise
        except Exception as e:
            if is_fatal(e):
                raise
            return fn()

    def _finish_chapter(self, novel: Novel, chapter: Chapter, mem_base, on_stage=None) -> None:
        """章节正文保存后的步骤：摘要 → 记忆 → 自动审校"""
        chapter_no = int(chapter.chapter_no)
        mem_text = mem_base.context_text()

        # 4. 生成摘要（已有摘要说明上次已完成这一步）
        if not (chapter.summary or "").strip():
            on_stage and on_stage("summary", "正文已保存，正在生成章节摘要...")
            try:
                chapter.summary = self._retry_once(lambda: self.generate_summary(novel, chapter, mem_text))
                self.db.commit()
                on_stage and on_stage("summary_done", "摘要生成完成")
            except Exception as e:
                raise RuntimeError(f"第{chapter_no}章已保存，但摘要生成失败（可重试补做）：{e}") from e

        # 5. 更新小说记忆
        on_stage and on_stage("memory", "正在更新小说记忆...")
        try:
            warning = self._retry_once(lambda: self.update_memory(novel, chapter, mem_base))
            on_stage and on_stage("memory_done", "小说记忆已更新" + (f"（{warning}）" if warning else ""))
        except Exception as e:
            raise RuntimeError(f"第{chapter_no}章已保存，但记忆更新失败（可重试补做）：{e}") from e

        # 6. 自动一致性审校（每 N 章）
        interval = int(float(config_value(self.db, "consistency_auto_interval", 0)))
        if interval > 0 and chapter_no % interval == 0:
            try:
                task_service.enqueue(self.db, "consistency_check", novel.id, {"chapter_no": chapter_no})
                on_stage and on_stage("consistency_queued", f"已自动安排第{chapter_no}章后的一致性审校")
            except Exception as e:
                on_stage and on_stage("consistency_queued", "自动审校入队失败：" + str(e))

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
        mem_text = memory_store.memory_before(self.db, novel, chapter_no).context_text()
        chapter.summary = self.generate_summary(novel, chapter, mem_text)
        chapter.updated_at = now()
        self.db.commit()
        on_stage and on_stage("done", "摘要生成完成")

    def generate_summary(self, novel: Novel, chapter: Chapter, memory_text: str | None = None) -> str:
        system = prompt_service.render(self.db, "chapter_summary")
        if memory_text is None:
            memory_text = memory_store.memory_before(self.db, novel, chapter.chapter_no).context_text()
        context = (
            context_builder.setting_text(novel)
            + "\n\n【小说记忆（当前状态）】\n" + memory_text
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

    def update_memory(self, novel: Novel, chapter: Chapter | None = None,
                      base: "memory_store.MemoryBase | None" = None) -> str:
        """用第 N 章正文更新记忆：旧记忆 = 第 N 章开始前的记忆，结果存为第 N 章的快照。

        - chapter 不传：取最后一章（后台"更新记忆"按钮）
        - base 不传：按快照自动推断（生成章节时由调用方在保存章节前算好传入）
        - 只有 N 是最后一章时才覆盖全书最新记忆；重写中间章节只更新该章快照
        返回需要提示用户的警告（没有则为空串）。
        """
        if chapter is None:
            chapter = (
                self.db.query(Chapter)
                .filter(Chapter.novel_id == novel.id, Chapter.status.in_(CHAPTER_WRITTEN_STATUSES))
                .order_by(Chapter.chapter_no.desc())
                .first()
            )
        if not chapter:
            raise RuntimeError("还没有已发布的章节，无法更新记忆")
        if base is None:
            base = memory_store.memory_before(self.db, novel, chapter.chapter_no)

        old_text = (
            memory_service.to_context_text(base.content) if (base.content or "").strip()
            else "（暂无旧记忆，这是第一次建立记忆）"
        )

        chapter_content = chapter.content or ""
        if len(chapter_content) > 6000:
            chapter_content = chapter_content[:6000] + "\n……（正文截断）"

        system = prompt_service.render(self.db, "memory_update")
        user = (
            f"【旧记忆】\n{old_text}\n\n"
            f"【最新一章】\n第{chapter.chapter_no}章 {chapter.title}\n"
            f"本章摘要：{chapter.summary}\n\n"
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
        # 增量合并：AI 漏掉的未回收伏笔、物品、世界观事实补回，删掉的人物移入名册
        data, restored = memory_service.merge_memory(base.content, data)
        new_content = json.dumps(data, ensure_ascii=False, indent=2)

        no = int(chapter.chapter_no)
        # 旧数据还没有快照：写新章节时把"开始前"的最新记忆补存为上一章快照，以后重写本章就有基准了
        if base.source == memory_store.CURRENT and no > 1 \
                and not memory_store.get_snapshot(self.db, novel.id, no - 1) and (base.content or "").strip():
            memory_store.save_snapshot(self.db, novel.id, no - 1, base.content)
        memory_store.save_snapshot(self.db, novel.id, no, new_content)

        warning = ""
        if restored:
            print(f"[memory] 第{no}章记忆补回 {len(restored)} 条：" + "；".join(restored[:5]))
        is_latest = no >= memory_store.max_chapter_no(self.db, novel.id)
        if is_latest:
            memory_row = self.db.query(NovelMemory).filter(NovelMemory.novel_id == novel.id).first()
            if not memory_row:
                memory_row = NovelMemory(novel_id=novel.id)
                self.db.add(memory_row)
            memory_row.content = new_content
            memory_row.updated_at = now()
            if base.source == memory_store.CURRENT_STALE:
                warning = "此前没有记忆快照，旧版本本章的内容可能仍残留在记忆中，建议到「记忆」页检查"
        else:
            warning = f"只更新了第{no}章的记忆快照；第{no + 1}章之后的记忆仍基于旧版本，后续章节如有矛盾需手动调整"
        self.db.commit()
        return warning

    # ============ 一致性审校 ============

    def check_consistency(self, novel: Novel, task, on_stage=None) -> None:
        on_stage and on_stage("request", "AI 正在审校大纲与正文的一致性...")
        last = (
            self.db.query(Chapter)
            .filter(Chapter.novel_id == novel.id, Chapter.status.in_(CHAPTER_WRITTEN_STATUSES))
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
        # 抽查最近几章正文：只看摘要发现不了"断了的手又能拔剑"这类正文层面的矛盾
        sample = context_builder.recent_text_sample(self.db, novel)
        if sample:
            user += (
                "\n\n【最近章节正文（抽查）】\n" + sample
                + "\n\n请特别检查以上正文与小说记忆、已写章节进度之间的细节矛盾"
                  "（人物伤势/能力/位置、物品归属、时间先后、已死亡或离场的人物再次出现等），"
                  "并在 related_chapters 中给出具体章号。"
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
