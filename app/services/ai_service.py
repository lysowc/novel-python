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
    CHAPTER_WRITTEN_STATUSES, NovelAutomation, Chapter, ConsistencyReport, Idea, Novel, NovelMemory, NovelSetting,
)
from app.services import context_builder, memory as memory_service
from app.services import chapter_versions, memory_store, prompt_service, quality, retrieval, task_service
from app.services import story_bible
from app.services.ai_client import CallbackAbort
from app.services.ai_client import AiClient, config_value

MEMORY_CHAPTER_CHARS = 12000  # 更新记忆时最多给 AI 看的正文字数（超出时保留开头和结尾）
# 补充规则放在 user 消息里：库里已有的 memory_patch prompt 不会随默认值更新
MEMORY_PATCH_RULES = (
    "【伏笔记录规则】\n"
    "- 伏笔只记会在后面章节回收的悬念/线索；本章内就会揭晓的小疑问、单纯的场景细节不要记，"
    "未决的小事放 unresolved_add。每章新增伏笔一般不超过 3 条\n"
    "- 关系到全书主线、必须在后面交代清楚的伏笔，写成 {\"description\": \"...\", \"major\": true}；"
    "旧记忆里已有、但其实是主线的，把描述放进 foreshadowing_major 列表\n"
    "- 回收伏笔时照抄旧记忆里的描述；旧记忆只列出了和本章相关的部分，没列出的不代表已删除，不要重复添加\n"
    "- 有人物死亡（哪怕是小角色）：在 profiles 里记这个人，status 写「已死亡：怎么死的（第N章）」；假死、重伤不算"
)

OUTLINE_SYNC_SYSTEM = (
    "你是长篇小说的大纲编辑。作者写正文时常常偏离原大纲（改了谁死谁活、证据去向、人物关系、事件顺序）。"
    "你的任务：对照本章原大纲和本章实际写出的内容，判断后续几章的大纲还能不能接上；接不上的，改写那几章的大纲。\n"
    "原则：\n"
    "1. 已写的正文是事实，大纲去适应正文，不能要求正文改回去\n"
    "2. 只改确实接不上或已经提前写过的章节；能接上的不要动，措辞也不要改\n"
    "3. 保住主线方向和后面的关键转折、结局；必要时用新的情节把剧情引回主线\n"
    "4. 被正文提前写掉的情节节点，后面的章节不要再写一遍；死了的人后面不能再活着出场\n"
    "只输出 JSON：{\"deviation\": \"无|轻微|明显\", \"reason\": \"一句话说明偏离了什么\", "
    "\"chapters\": [{\"no\": 章号, \"title\": \"章标题\", \"summary\": \"本章目标\"}]}，"
    "chapters 只放需要修改的章节，不需要修改就给空数组"
)
OUTLINE_SYNC_WINDOW = 8  # 每次最多调整后面这么多章的大纲



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

        # 大纲快用完：开启了自动续写大纲就先补上（失败不影响写本章）
        if type_ != "regenerate_chapter":
            self._maybe_extend_outline(novel, chapter_no, on_stage)

        target_words = int(params.get("target_words") or 0) or int(
            float(config_value(self.db, "chapter_target_words", 3000)))
        instruction = str(params.get("instruction") or "").strip()
        max_tokens = max(1024, min(16384, target_words * 2))

        # 0. 本章开始前的记忆（重写中间章节时用当时的快照，而不是全书最新记忆）
        mem_base = memory_store.memory_before(self.db, novel, chapter_no)
        # 次要伏笔、世界观按与本章（大纲/上一章结尾）的相关性挑选，主线伏笔全带
        mem_text = mem_base.context_text(context_builder.chapter_hint(self.db, novel, chapter_no, instruction))
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
            self.db, novel, chapter_no, instruction, retrieved_list, mem_text, output_tokens=max_tokens)

        content, issues = self._write_with_quality_check(
            novel, chapter_no, system, user_context, type_, max_tokens, target_words, on_delta, on_stage)
        if issues:
            task.params = {**(task.params or {}), "quality_warning": "；".join(issues)}
            on_stage and on_stage("quality_warning", "自动重写后仍有问题，已保留较好的一版：" + "；".join(issues))

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
        # 新章节用大纲标题；重新生成时保留现有标题（可能是作者手动改过的），只有空标题才补大纲标题
        if not (chapter.title or "").strip():
            chapter.title = context_builder.outline_title(novel, chapter_no)
        chapter.content = content
        chapter.summary = ""  # 旧摘要对应旧正文，清空，由下一步重新生成
        chapter.word_count = word_count(content)
        chapter.updated_at = now()  # 重新生成保留原状态（已发布的不会因重写而对读者消失）
        # 记下断点：后续步骤失败时，重试只补做摘要/记忆，不会重新生成或多写一章
        task.params = {**(task.params or {}), "resume": {"chapter_no": chapter_no, "memory_base": mem_base.source}}
        self.db.commit()
        self._recount(novel)

        self._finish_chapter(novel, chapter, mem_base, on_stage, task)
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
        self._finish_chapter(novel, chapter, mem_base, on_stage, task)
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

    def _finish_chapter(self, novel: Novel, chapter: Chapter, mem_base, on_stage=None, task=None) -> None:
        """章节正文保存后的步骤：摘要 → 记忆 → 自动审校"""
        chapter_no = int(chapter.chapter_no)
        mem_text = mem_base.context_text(chapter.content or "")

        # 4. 生成摘要（已有摘要说明上次已完成这一步）
        if not (chapter.summary or "").strip():
            on_stage and on_stage("summary", "正文已保存，正在生成章节摘要...")
            try:
                chapter.summary = self._retry_once(lambda: self.generate_summary(novel, chapter, mem_text))
                self.db.commit()
                on_stage and on_stage("summary_done", "摘要生成完成")
            except Exception as e:
                raise RuntimeError(f"第{chapter_no}章已保存，但摘要生成失败（可重试补做）：{e}") from e

        # 4.1 与前文重复的桥段（换了名字又写一遍）：只提示，不拦截
        if int(float(config_value(self.db, "repetition_check_enabled", 1))) > 0:
            hits = story_bible.repetition_check(self.db, novel.id, chapter_no, chapter.summary or "")
            if hits:
                msg = "本章情节与" + "、".join(f"第{n}章（相似度{int(sc * 100)}%）" for n, sc in hits) + "相似，注意是否重复套路"
                if task is not None:
                    task.params = {**(task.params or {}), "repetition_warning": msg}
                    self.db.commit()
                on_stage and on_stage("repetition_warning", msg)

        has_later = self.db.query(Chapter.id).filter(
            Chapter.novel_id == novel.id, Chapter.chapter_no > chapter_no).first() is not None
        auto_rebuild = int(float(config_value(self.db, "auto_rebuild_memory", 1))) > 0
        # 重写已有章节：旧版本本章（以及要重算的后续章节）留下的人物档案、名词、境界变化先撤回
        if memory_store.get_snapshot(self.db, novel.id, chapter_no) and (not has_later or auto_rebuild):
            story_bible.reset_from(self.db, novel.id, chapter_no)
            self.db.commit()
        story_bible.invalidate_arc(self.db, novel.id, chapter_no)
        self.db.commit()

        # 5. 更新小说记忆
        on_stage and on_stage("memory", "正在更新小说记忆...")
        try:
            warning = self._retry_once(lambda: self.update_memory(novel, chapter, mem_base))
        except Exception as e:
            raise RuntimeError(f"第{chapter_no}章已保存，但记忆更新失败（可重试补做）：{e}") from e

        # 5.1 重写的是中间章节：后续章节的记忆都建立在旧版本上，按设置自动逐章重建
        if has_later and auto_rebuild:
            on_stage and on_stage("memory_done", f"第{chapter_no}章记忆已更新，开始重建后续章节的记忆...")
            try:
                self.rebuild_memory(novel, chapter_no + 1, on_stage)
            except Exception as e:
                raise RuntimeError(
                    f"第{chapter_no}章已保存，但后续章节记忆重建失败（可重试，或在记忆页从第{chapter_no + 1}章重建）：{e}"
                ) from e
        else:
            on_stage and on_stage("memory_done", "小说记忆已更新" + (f"（{warning}）" if warning else ""))

        # 5.2 分段总结：写满一段（默认 20 章）就压缩成一段前情提要；失败不影响本章
        self.fill_arc_summaries(novel, on_stage)

        # 5.3 大纲校准：正文偏离了大纲时，把后面几章的大纲改到能接上（只在写最新一章时做；失败不影响本章）
        if not has_later and int(float(config_value(self.db, "outline_sync_enabled", 1))) > 0:
            try:
                changed = self.sync_outline(novel, chapter, on_stage)
                if changed and task is not None:
                    task.params = {**(task.params or {}), "outline_adjusted": changed}
                    self.db.commit()
            except CallbackAbort:
                raise
            except Exception as e:
                on_stage and on_stage("outline_sync_failed", f"大纲校准失败（不影响本章）：{e}")

        # 6. 自动一致性审校（每 N 章）
        interval = int(float(config_value(self.db, "consistency_auto_interval", 10)))
        if interval > 0 and chapter_no % interval == 0:
            try:
                task_service.enqueue(self.db, "consistency_check", novel.id, {"chapter_no": chapter_no})
                on_stage and on_stage("consistency_queued", f"已自动安排第{chapter_no}章后的一致性审校")
            except Exception as e:
                on_stage and on_stage("consistency_queued", "自动审校入队失败：" + str(e))

    def _write_with_quality_check(self, novel: Novel, chapter_no: int, system: str, user_context: str,
                                  type_: str, max_tokens: int, target_words: int,
                                  on_delta=None, on_stage=None) -> tuple[str, list[str]]:
        """生成正文；开启质量检查时，字数不足/截断/与上一章大段重复/段落重复会自动重写一次，保留较好的一版"""
        enabled = int(float(config_value(self.db, "quality_check_enabled", 1))) > 0
        try:
            min_ratio = float(config_value(self.db, "quality_min_ratio", 0.5))
        except (TypeError, ValueError):
            min_ratio = 0.5
        previous = self.db.query(Chapter.content).filter(
            Chapter.novel_id == novel.id, Chapter.chapter_no == chapter_no - 1).scalar() or ""

        user = user_context
        best: tuple[str, list[str]] | None = None
        attempts = 2 if enabled else 1
        for attempt in range(1, attempts + 1):
            result = self.client.chat_stream(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                {"task_type": type_, "max_tokens": max_tokens},
                on_delta, on_stage,
            )
            content = (result.get("text") or "").strip()
            if not enabled:
                return content, []
            issues = quality.check(content, target_words, previous, result.get("finish_reason", ""), min_ratio) \
                if content else ["AI 返回内容为空"]
            if best is None or quality.score(issues, content) > quality.score(best[1], best[0]):
                best = (content, issues)
            if not issues or attempt == attempts:
                break
            on_stage and on_stage("quality_retry", "质量检查未通过：" + "；".join(issues) + "，自动重写一次...")
            on_stage and on_stage("reset_output", "")
            user = (user_context + "\n\n【上一次生成的问题】\n" + "\n".join(f"- {i}" for i in issues)
                    + "\n请避免以上问题，完整写完本章。")
        content, issues = best
        if not content:
            raise RuntimeError("AI 返回内容为空")
        if content != (result.get("text") or "").strip():
            # 保留的是第一版：让进度框重新显示它
            on_stage and on_stage("reset_output", "")
            on_delta and on_delta(content)
        return content, issues

    def _maybe_extend_outline(self, novel: Novel, chapter_no: int, on_stage=None) -> None:
        automation = self.db.query(NovelAutomation).filter(NovelAutomation.novel_id == novel.id).first()
        if not automation or not automation.auto_extend_outline:
            return
        planned = context_builder.outline_max_no(novel)
        threshold = int(float(config_value(self.db, "outline_warn_remaining", 3)))
        if planned <= 0 or planned - chapter_no + 1 > threshold:
            return
        try:
            self.extend_outline(novel, int(automation.extend_chapters or 10), on_stage)
        except CallbackAbort:
            raise
        except Exception as e:  # noqa: BLE001  大纲续写失败不影响写本章
            on_stage and on_stage("outline_extend_failed", f"自动续写大纲失败（不影响本章）：{e}")

    def extend_outline(self, novel: Novel, chapters: int = 10, on_stage=None) -> int:
        """接着现有大纲往后规划 chapters 章，返回新增章数"""
        chapters = max(1, min(50, int(chapters or 10)))
        outline = novel.get_outline_array()
        volumes = [v for v in outline.get("volumes", []) if isinstance(v, dict)]
        start_no = context_builder.outline_max_no(novel) + 1
        on_stage and on_stage("outline_extend", f"大纲快用完了，正在续写第{start_no}章起的 {chapters} 章大纲...")
        outline_lines = context_builder.outline_text(novel).splitlines()
        if len(outline_lines) > 80:
            outline_lines = ["……（更早的大纲已省略）"] + outline_lines[-80:]
        system = prompt_service.render(self.db, "outline_extend", {"chapters": chapters, "start_no": start_no})
        user = (
            context_builder.setting_text(novel)
            + "\n\n【现有大纲】\n" + ("\n".join(outline_lines) or "（暂无）")
            + "\n\n【已写章节进度】\n" + context_builder.written_progress_text(self.db, novel, 8000)
            + "\n\n【小说记忆（当前状态）】\n" + context_builder.memory_text(novel)
        )
        timeout = int(float(config_value(self.db, "ai_http_timeout", 120))) * 3
        result = self.client.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            {"task_type": "extend_outline", "max_tokens": 8192, "timeout": timeout},
        )
        data = extract_json(result["text"])
        if not isinstance(data.get("volumes"), list):
            raise RuntimeError("大纲续写解析失败：AI 未返回合法 JSON")
        no = start_no
        for volume in data["volumes"]:
            if not isinstance(volume, dict):
                continue
            new_chapters = []
            for chapter in volume.get("chapters") or []:
                if not isinstance(chapter, dict) or no >= start_no + chapters:
                    continue
                new_chapters.append({"no": no, "title": str(chapter.get("title") or f"第{no}章"),
                                     "summary": str(chapter.get("summary") or "")})
                no += 1
            if not new_chapters:
                continue
            title = str(volume.get("title") or "").strip()
            if volumes and (not title or title == volumes[-1].get("title")):
                volumes[-1].setdefault("chapters", []).extend(new_chapters)
            else:
                volumes.append({"title": title or f"第{len(volumes) + 1}卷", "chapters": new_chapters})
        added = no - start_no
        if added <= 0:
            raise RuntimeError("大纲续写失败：AI 没有给出新的章节")
        novel.outline = json.dumps({"volumes": volumes}, ensure_ascii=False)
        novel.updated_at = now()
        self.db.commit()
        on_stage and on_stage("outline_extended", f"大纲已续写 {added} 章（第{start_no}~{no - 1}章）")
        return added

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
        mem_text = memory_store.memory_before(self.db, novel, chapter_no).context_text(chapter.content or "")
        chapter.summary = self.generate_summary(novel, chapter, mem_text)
        chapter.updated_at = now()
        self.db.commit()
        on_stage and on_stage("done", "摘要生成完成")

    def generate_summary(self, novel: Novel, chapter: Chapter, memory_text: str | None = None) -> str:
        system = prompt_service.render(self.db, "chapter_summary")
        if memory_text is None:
            memory_text = memory_store.memory_before(self.db, novel, chapter.chapter_no).context_text(
                chapter.content or "")
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

        # 旧记忆按与本章正文的相关性挑选（主线伏笔全带）：几百章后旧记忆也不会撑爆输入；
        # 没列出的条目不会丢——补丁只写变化，回收伏笔时按描述在全部记忆里模糊匹配
        old_text = (
            memory_service.to_context_text(base.content, chapter.content or "") if (base.content or "").strip()
            else "（暂无旧记忆，这是第一次建立记忆）"
        )

        no = int(chapter.chapter_no)
        chapter_content = chapter.content or ""
        if len(chapter_content) > MEMORY_CHAPTER_CHARS:
            # 保留开头和结尾：结尾决定"本章结束时"的状态，比中间更重要
            head = MEMORY_CHAPTER_CHARS * 2 // 3
            chapter_content = chapter_content[:head] + "\n……（中间省略）……\n" \
                + chapter_content[-(MEMORY_CHAPTER_CHARS - head):]

        system = prompt_service.render(self.db, "memory_patch")
        user = (
            f"【旧记忆】\n{old_text}\n\n"
            f"【人物档案索引】\n{story_bible.profile_brief(self.db, novel.id)}\n\n"
            f"【名词表】\n{story_bible.term_brief(self.db, novel.id)}\n\n"
            f"{MEMORY_PATCH_RULES}\n\n"
            f"【最新一章】\n第{no}章 {chapter.title}\n"
            f"本章摘要：{chapter.summary}\n\n"
            f"本章正文：\n{chapter_content}"
        )
        result = self.client.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            {"task_type": "update_memory", "max_tokens": 6144},
        )
        data = extract_json(result["text"])
        if not data:
            raise RuntimeError("记忆更新失败：AI 未返回合法 JSON")
        if memory_service.is_patch(data):
            # 增量补丁：旧记忆里没提到的内容原样保留
            patch = data
            data, notes = memory_service.apply_patch(base.content, patch, no)
            notes += story_bible.upsert_profiles(self.db, novel.id, no, patch.get("profiles"))
            notes += story_bible.upsert_terms(self.db, novel.id, no, patch.get("terms"))
            restored = []
            if notes:
                print(f"[memory] 第{no}章资料库：" + "；".join(notes[:8]))
        else:
            # 兼容：AI（或作者改过的旧 prompt）返回了整份记忆 → 规整后与旧记忆合并兜底
            data = memory_service.normalize_memory(data)
            data, restored = memory_service.merge_memory(base.content, data)
        story_bible.mark_appearances(self.db, novel.id, no, chapter.content or "")
        new_content = json.dumps(data, ensure_ascii=False, indent=2)

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
                warning = "此前没有记忆快照，旧版本本章的内容可能仍残留在记忆中，可在「记忆」页从第 1 章重建记忆"
        else:
            warning = f"只更新了第{no}章的记忆快照；第{no + 1}章之后的记忆仍基于旧版本，可在「记忆」页从第{no}章重建记忆"
        self.db.commit()
        return warning

    def rebuild_memory(self, novel: Novel, start_no: int = 1, on_stage=None) -> None:
        """从第 start_no 章起逐章重算记忆（每章一次 AI 调用），最后一章的结果成为全书最新记忆。

        用于：重写了中间章节后让后续记忆跟上；旧小说没有快照时从第 1 章完整重建。
        注意：会覆盖在「记忆」页手动修改过的内容。
        """
        chapters = (
            self.db.query(Chapter)
            .filter(Chapter.novel_id == novel.id, Chapter.status.in_(CHAPTER_WRITTEN_STATUSES),
                    Chapter.chapter_no >= max(1, start_no))
            .order_by(Chapter.chapter_no)
            .all()
        )
        if not chapters:
            raise RuntimeError(f"第{start_no}章及之后没有章节，无需重建")
        first_no = int(chapters[0].chapter_no)
        has_earlier = self.db.query(Chapter.id).filter(
            Chapter.novel_id == novel.id, Chapter.chapter_no < first_no).first() is not None
        snap = memory_store.snapshot_before(self.db, novel.id, first_no)
        if has_earlier and not snap:
            raise RuntimeError(f"第{first_no}章之前没有记忆快照，无法从这里接着算，请从第 1 章开始重建")
        base = memory_store.MemoryBase(snap.content or "", memory_store.SNAPSHOT, snap.chapter_no) if snap \
            else memory_store.MemoryBase("", memory_store.EMPTY)

        # AI 在这些章节建的档案/名词、记下的境界变化先撤回，重算时重新提取（作者锁定的不动）
        story_bible.reset_from(self.db, novel.id, first_no)
        self.db.commit()

        total = len(chapters)
        for idx, chapter in enumerate(chapters, 1):
            on_stage and on_stage("rebuild", f"正在重算第{chapter.chapter_no}章的记忆（{idx}/{total}）...")
            self._retry_once(lambda c=chapter, b=base: self.update_memory(novel, c, b))
            row = memory_store.get_snapshot(self.db, novel.id, chapter.chapter_no)
            base = memory_store.MemoryBase(row.content or "", memory_store.SNAPSHOT, chapter.chapter_no)
        on_stage and on_stage("done", f"记忆重建完成：第{first_no}章至第{chapters[-1].chapter_no}章，共 {total} 章")

    # ============ 分段总结 ============

    def generate_arc_summary(self, novel: Novel, start_no: int, end_no: int) -> str:
        written_max = int(self.db.query(func.max(Chapter.chapter_no)).filter(
            Chapter.novel_id == novel.id, Chapter.status.in_(CHAPTER_WRITTEN_STATUSES)).scalar() or 0)
        if written_max < end_no:
            raise RuntimeError(f"第{start_no}-{end_no}章这一段还没写完（已写到第{written_max}章）")
        source = story_bible.arc_source_text(self.db, novel.id, start_no, end_no)
        if not source.strip():
            raise RuntimeError(f"第{start_no}-{end_no}章没有内容")
        earlier = story_bible.arcs_text(self.db, novel.id, start_no)
        user = (("【更早的前情】\n" + earlier[-3000:] + "\n\n") if earlier else "") \
            + f"【第{start_no}-{end_no}章摘要】\n{source}"
        result = self.client.chat(
            [{"role": "system", "content": prompt_service.render(self.db, "arc_summary")},
             {"role": "user", "content": user}],
            {"task_type": "arc_summary", "max_tokens": 2048},
        )
        text = (result.get("text") or "").strip()
        if not text:
            raise RuntimeError("AI 返回内容为空")
        story_bible.save_arc(self.db, novel.id, start_no, end_no, text)
        self.db.commit()
        return text

    def sync_outline(self, novel: Novel, chapter: Chapter, on_stage=None) -> str:
        """对照本章原大纲和实际写出的内容，调整后续 OUTLINE_SYNC_WINDOW 章的大纲。返回改动说明（没改返回空串）"""
        chapter_no = int(chapter.chapter_no)
        outline = novel.get_outline_array()
        volumes = [v for v in outline.get("volumes", []) if isinstance(v, dict)]
        entries = [c for v in volumes for c in (v.get("chapters") or []) if isinstance(c, dict)]
        window = [c for c in entries if chapter_no < int(c.get("no") or 0) <= chapter_no + OUTLINE_SYNC_WINDOW]
        if not window:
            return ""
        later = [c for c in entries if int(c.get("no") or 0) > chapter_no + OUTLINE_SYNC_WINDOW]

        def line(c):
            return f"第{c.get('no')}章 {c.get('title', '')}：{c.get('summary', '')}"

        on_stage and on_stage("outline_sync", "正在对照大纲检查本章是否偏离，必要时调整后续大纲...")
        user = (
            context_builder.setting_text(novel)[:6000]
            + "\n\n【本章原大纲】\n" + context_builder.outline_target(novel, chapter_no).split("\n（")[0]
            + f"\n\n【本章实际写了什么（第{chapter_no}章摘要）】\n{chapter.summary or ''}"
            + "\n\n【本章结尾】\n" + (chapter.content or "")[-1500:]
            + "\n\n【小说记忆（当前状态）】\n"
            + memory_service.to_context_text(memory_store.current_content(self.db, novel.id), chapter.content or "")
            + "\n\n【已死亡人物】\n" + ("、".join(p.name for p in story_bible.dead_profiles(self.db, novel.id)) or "（无）")
            + f"\n\n【后续大纲（可调整，第{chapter_no + 1}~{chapter_no + OUTLINE_SYNC_WINDOW}章）】\n"
            + "\n".join(line(c) for c in window)
            + "\n\n【更后面的大纲（只读，用来把握主线方向，不要修改）】\n"
            + ("\n".join(line(c) for c in later[:40]) or "（无）")
        )
        result = self.client.chat(
            [{"role": "system", "content": OUTLINE_SYNC_SYSTEM}, {"role": "user", "content": user}],
            {"task_type": "outline_sync", "max_tokens": 4096},
        )
        data = extract_json(result["text"])
        if not data:
            raise RuntimeError("AI 未返回合法 JSON")
        allowed = {int(c.get("no") or 0): c for c in window}
        changed = []
        for item in data.get("chapters") or []:
            if not isinstance(item, dict):
                continue
            no = int(item.get("no") or 0) if str(item.get("no") or "").strip().isdigit() else 0
            summary = str(item.get("summary") or "").strip()
            if no not in allowed or not summary:
                continue
            target = allowed[no]
            if summary == str(target.get("summary") or "").strip():
                continue
            target["summary"] = summary
            title = str(item.get("title") or "").strip()
            if title:
                target["title"] = title
            changed.append(no)
        if not changed:
            on_stage and on_stage("outline_sync_done", "本章与大纲衔接正常，后续大纲无需调整")
            return ""
        novel.outline = json.dumps({**outline, "volumes": volumes}, ensure_ascii=False)
        novel.updated_at = now()
        self.db.commit()
        reason = str(data.get("reason") or "").strip()
        msg = ("已按本章实际内容调整第" + "、".join(str(n) for n in sorted(changed)) + "章大纲"
               + (f"：{reason}" if reason else ""))
        print(f"[outline] 第{chapter_no}章后{msg}")
        on_stage and on_stage("outline_sync_done", msg)
        return msg

    def fill_arc_summaries(self, novel: Novel, on_stage=None, raise_errors: bool = False) -> int:
        """补齐所有已写满但还没有（或已作废）的分段总结，返回生成的段数"""
        done = 0
        for start_no, end_no in story_bible.missing_arcs(self.db, novel.id):
            on_stage and on_stage("arc", f"正在生成第{start_no}-{end_no}章的分段总结...")
            try:
                self._retry_once(lambda a=start_no, b=end_no: self.generate_arc_summary(novel, a, b))
                done += 1
            except CallbackAbort:
                raise
            except Exception as e:
                if raise_errors:
                    raise
                on_stage and on_stage("arc_failed", f"第{start_no}-{end_no}章分段总结生成失败（不影响正文，可在资料库重试）：{e}")
                break
        if done:
            on_stage and on_stage("arc_done", f"已生成 {done} 段分段总结")
        return done

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
        # 资料库：人物档案（人设、境界变化）、名词表、分段总结 —— 用来查人设漂移、境界倒退、名词写法不一
        profile_rows = sorted(story_bible.profiles(self.db, novel.id), key=lambda r: r.last_chapter or 0, reverse=True)[:40]
        if profile_rows:
            lines = []
            for p in profile_rows:
                history = "→".join(f"{h.get('realm')}(第{h.get('chapter')}章)" for h in (p.realm_history or [])
                                   if isinstance(h, dict))
                lines.append(story_bible.render_profiles([p]) + (f"\n  境界变化：{history}" if history else ""))
            user += "\n\n【人物档案】\n" + "\n".join(lines)
        term_rows = story_bible.terms(self.db, novel.id)
        if term_rows:
            user += "\n\n【名词表】\n" + story_bible.render_terms(term_rows[-80:])
        arcs = story_bible.arcs_text(self.db, novel.id)
        if arcs:
            user += "\n\n【前情总览（分段总结）】\n" + arcs[-6000:]
        user += (
            "\n\n另外请检查：人物言行是否违背【人物档案】中的性格设定、境界是否无故倒退或跳级、"
            "专有名词是否出现不同写法、近期章节是否反复使用同一种桥段套路（如同样的冲突-打脸-突破模式连续出现），"
            "套路重复请用 type=other 并在 description 中写明\u201c重复套路\u201d。"
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
