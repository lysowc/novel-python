"""短剧改编：已写章节 → 分集方案 → 每集剧本 → 分镜

- 分集（drama_plan）：从上次改编到的章节往后，每次给 AI 几章正文，切成一集一集（标题、对应章节、梗概、开头钩子、结尾钩子）
- 剧本（drama_script）：按分集方案 + 原著章节写成可拍的剧本；带上前一集结尾和下一集方案，保证接得上、不抢演下一集
- 分镜（drama_storyboard）：把剧本拆成一个个镜头（景别、运镜、时长、画面、台词、音效），供以后生成视频
人物口吻、关系、外形都从资料库的人物档案里取，跟小说保持一致。
"""
import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.helpers import now
from app.models import CHAPTER_WRITTEN_STATUSES, Chapter, DramaEpisode, Novel
from app.services import context_builder, prompt_service, story_bible
from app.services.ai_client import config_value

PLAN_WINDOW = 5            # 每次给 AI 几章来切分集
SOURCE_CAP = 24000         # 写剧本时带的原著正文上限（字）
PREV_SCRIPT_TAIL = 1200    # 带上前一集剧本的结尾多少字
DEFAULT_SECONDS = 90


def episode_seconds(db: Session) -> int:
    try:
        return min(600, max(30, int(float(config_value(db, "drama_episode_seconds", DEFAULT_SECONDS)))))
    except (TypeError, ValueError):
        return DEFAULT_SECONDS


def episodes(db: Session, novel_id: int) -> list[DramaEpisode]:
    return db.query(DramaEpisode).filter(DramaEpisode.novel_id == novel_id).order_by(DramaEpisode.episode_no).all()


def covered_until(db: Session, novel_id: int) -> int:
    """分集方案已经改编到第几章"""
    return int(db.query(func.max(DramaEpisode.chapter_to)).filter(DramaEpisode.novel_id == novel_id).scalar() or 0)


def written_chapters(db: Session, novel_id: int, start: int = 1, end: int | None = None) -> list[Chapter]:
    q = db.query(Chapter).filter(Chapter.novel_id == novel_id, Chapter.status.in_(CHAPTER_WRITTEN_STATUSES),
                                 Chapter.chapter_no >= start)
    if end:
        q = q.filter(Chapter.chapter_no <= end)
    return [c for c in q.order_by(Chapter.chapter_no).all() if (c.content or "").strip()]


def load_storyboard(ep: DramaEpisode) -> list[dict]:
    try:
        data = json.loads(ep.storyboard or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _profiles_in(db: Session, novel_id: int, text: str, appearance_only: bool = False) -> str:
    rows = []
    for p in story_bible.profiles(db, novel_id):
        names = [p.name] + [a.strip() for a in (p.aliases or "").replace("，", ",").replace("、", ",").split(",")]
        if any(n and n in text for n in names):
            rows.append(p)
    if not rows:
        return ""
    if appearance_only:
        return "\n".join(f"- {p.name}" + (f"（{p.role}）" if p.role else "") + "：" + ((p.appearance or "").strip() or "外形未设定")
                         for p in rows)
    return story_bible.render_profiles(rows)


def _ep_head(ep: DramaEpisode) -> str:
    src = f"第{ep.chapter_from}章" if ep.chapter_from == ep.chapter_to else f"第{ep.chapter_from}-{ep.chapter_to}章"
    return f"第{ep.episode_no}集《{ep.title}》（改编自{src}）"


def _ep_plan_text(ep: DramaEpisode) -> str:
    return (_ep_head(ep) + f"\n梗概：{ep.summary or ''}\n开头钩子：{ep.hook or ''}\n结尾钩子：{ep.cliffhanger or ''}")


# ---------- 分集方案 ----------

def plan(ai, novel: Novel, task, on_stage=None) -> int:
    """把还没改编的章节切成分集，接着已有分集往后编号；返回新增集数"""
    db = ai.db
    params = task.params or {}
    start = covered_until(db, novel.id) + 1
    end = int(params.get("chapter_to") or 0) or None
    chapters = written_chapters(db, novel.id, start, end)
    if not chapters:
        raise RuntimeError(f"没有需要改编的新章节（已改编到第{start - 1}章）")
    seconds = episode_seconds(db)
    system = prompt_service.render(db, "drama_plan", {"seconds": seconds})
    added = 0
    for i in range(0, len(chapters), PLAN_WINDOW):
        window = chapters[i:i + PLAN_WINDOW]
        lo, hi = window[0].chapter_no, window[-1].chapter_no
        on_stage and on_stage("request", f"正在把第{lo}-{hi}章切成分集…")
        existing = episodes(db, novel.id)
        prev = "\n\n".join(_ep_plan_text(e) for e in existing[-3:]) or "（还没有，从第1集开始）"
        source = "\n\n".join(f"第{c.chapter_no}章 {c.title}\n{c.content}" for c in window)
        user = (context_builder.setting_text(novel, for_chapter=True)
                + ("\n\n【人物】\n" + p if (p := _profiles_in(db, novel.id, source)) else "")
                + "\n\n【已有分集（最后几集）】\n" + prev
                + f"\n\n【这次要改编的章节：第{lo}-{hi}章】\n" + source
                + f"\n\n请把第{lo}-{hi}章切成分集，集号从第{(existing[-1].episode_no if existing else 0) + 1}集接着编。")
        result = ai.client.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                {"task_type": "drama_plan", "max_tokens": 8192})
        from app.services.ai_service import extract_json
        items = extract_json(result["text"]).get("episodes") or []
        no = existing[-1].episode_no if existing else 0
        last_from = lo
        made = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            rng = it.get("chapters") or []
            try:
                a = int(rng[0]) if rng else last_from
                b = int(rng[-1]) if rng else a
            except (TypeError, ValueError, IndexError):
                a = b = last_from
            a = min(max(a, last_from, lo), hi)
            b = min(max(b, a), hi)
            last_from = a
            no += 1
            db.add(DramaEpisode(
                novel_id=novel.id, episode_no=no, title=str(it.get("title") or f"第{no}集").strip()[:255],
                chapter_from=a, chapter_to=b, summary=str(it.get("summary") or "").strip(),
                hook=str(it.get("hook") or "").strip(), cliffhanger=str(it.get("cliffhanger") or "").strip(),
                created_at=now(), updated_at=now()))
            made += 1
        if not made:
            raise RuntimeError(f"第{lo}-{hi}章没有切出分集（AI 返回为空），可重试")
        # 最后一集至少覆盖到窗口最后一章，下次从下一章接着改编
        db.flush()
        last = episodes(db, novel.id)[-1]
        last.chapter_to = max(last.chapter_to, hi)
        db.commit()
        added += made
    on_stage and on_stage("done", f"新增 {added} 集分集方案（改编到第{chapters[-1].chapter_no}章）")
    return added


# ---------- 剧本 / 分镜 ----------

def _targets(db: Session, novel_id: int, step: str, params: dict) -> list[DramaEpisode]:
    rows = episodes(db, novel_id)
    ids = [int(i) for i in (params.get("episode_ids") or []) if str(i).isdigit()]
    if ids:
        return [e for e in rows if e.id in ids]
    if step == "drama_script":
        return [e for e in rows if not (e.script or "").strip()]
    return [e for e in rows if (e.script or "").strip() and not load_storyboard(e)]


def run_episodes(ai, novel: Novel, task, on_delta=None, on_stage=None) -> None:
    db = ai.db
    step = task.task_type
    targets = _targets(db, novel.id, step, task.params or {})
    label = "剧本" if step == "drama_script" else "分镜"
    if not targets:
        raise RuntimeError("没有需要写剧本的分集" if step == "drama_script"
                           else "没有需要出分镜的分集（要先有剧本）")
    failed: list[str] = []
    for k, ep in enumerate(targets, 1):
        on_stage and on_stage("request", f"（{k}/{len(targets)}）第{ep.episode_no}集《{ep.title}》：正在写{label}…")
        try:
            if step == "drama_script":
                on_delta and on_delta(("\n\n" if k > 1 else "") + f"【第{ep.episode_no}集 {ep.title}】\n\n")
                write_script(ai, novel, ep, on_delta)
            else:
                if not (ep.script or "").strip():
                    raise RuntimeError("还没有剧本")
                make_storyboard(ai, novel, ep)
            db.commit()
        except Exception as e:  # 一集失败不影响后面的集；取消照常中止
            if type(e).__name__ in ("TaskCancelled", "CallbackAbort", "AiFatalError", "BudgetExceeded"):
                raise
            db.rollback()
            failed.append(f"第{ep.episode_no}集（{str(e)[:80]}）")
    done = len(targets) - len(failed)
    if failed:
        raise RuntimeError(f"{label}完成 {done} 集，失败：" + "；".join(failed[:5]) + "，可重试")
    on_stage and on_stage("done", f"{done} 集{label}已完成")


def _source_text(db: Session, novel_id: int, ep: DramaEpisode) -> str:
    rows = written_chapters(db, novel_id, ep.chapter_from, ep.chapter_to)
    text = "\n\n".join(f"第{c.chapter_no}章 {c.title}\n{c.content}" for c in rows)
    return text[:SOURCE_CAP]


def write_script(ai, novel: Novel, ep: DramaEpisode, on_delta=None) -> str:
    db = ai.db
    seconds = episode_seconds(db)
    system = prompt_service.render(db, "drama_script", {"seconds": seconds, "words": seconds * 4})
    rows = episodes(db, novel.id)
    prev = next((e for e in reversed(rows) if e.episode_no < ep.episode_no), None)
    nxt = next((e for e in rows if e.episode_no > ep.episode_no), None)
    source = _source_text(db, novel.id, ep)
    user = context_builder.setting_text(novel, for_chapter=True)
    if p := _profiles_in(db, novel.id, source + (ep.summary or "")):
        user += "\n\n【人物（口吻、称呼、关系照这里）】\n" + p
    if prev:
        user += "\n\n【上一集】\n" + _ep_plan_text(prev)
        if (prev.script or "").strip():
            user += "\n上一集剧本结尾：\n" + prev.script.strip()[-PREV_SCRIPT_TAIL:]
    user += "\n\n【本集方案】\n" + _ep_plan_text(ep)
    if nxt:
        user += ("\n\n【下一集方案（只用来知道本集停在哪，下一集的内容本集一句都不要演）】\n"
                 + f"第{nxt.episode_no}集《{nxt.title}》：{nxt.summary or ''}")
    user += "\n\n【原著章节】\n" + source
    user += f"\n\n请写第{ep.episode_no}集的剧本。"
    result = ai.client.chat_stream([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                   {"task_type": "drama_script", "max_tokens": 8192}, on_delta)
    text = (result.get("text") or "").strip()
    if not text:
        raise RuntimeError("剧本为空")
    ep.script = text
    ep.script_at = now()
    ep.updated_at = now()
    return text


SHOT_FIELDS = ("scene", "shot", "camera", "visual", "line", "sound", "subtitle")


def parse_shots(data) -> list[dict]:
    items = data.get("shots") if isinstance(data, dict) else data
    shots = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        try:
            duration = float(it.get("duration") or 3)
        except (TypeError, ValueError):
            duration = 3
        chars = it.get("characters") or []
        if isinstance(chars, str):
            chars = [c.strip() for c in chars.replace("，", ",").replace("、", ",").split(",") if c.strip()]
        shot = {"no": len(shots) + 1, "duration": round(min(15.0, max(1.0, duration)), 1),
                "characters": [str(c)[:32] for c in chars if str(c).strip()][:8]}
        for key in SHOT_FIELDS:
            shot[key] = str(it.get(key) or "").strip()
        if shot["visual"] or shot["line"]:
            shots.append(shot)
    return shots


def make_storyboard(ai, novel: Novel, ep: DramaEpisode) -> list[dict]:
    db = ai.db
    seconds = episode_seconds(db)
    system = prompt_service.render(db, "drama_storyboard", {"seconds": seconds})
    user = ""
    if looks := _profiles_in(db, novel.id, ep.script or "", appearance_only=True):
        user += "【人物外形（画面里每次都按这个画，保证同一个人长得一样）】\n" + looks + "\n\n"
    user += f"【本集剧本】\n{ep.script}\n\n请把第{ep.episode_no}集剧本拆成分镜。"
    result = ai.client.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                            {"task_type": "drama_storyboard", "max_tokens": 16384})
    from app.services.ai_service import extract_json
    shots = parse_shots(extract_json(result["text"]))
    if not shots:
        raise RuntimeError("分镜为空")
    ep.storyboard = json.dumps(shots, ensure_ascii=False)
    ep.storyboard_at = now()
    ep.updated_at = now()
    return shots


def source_changed(db: Session, novel_id: int, rows: list[DramaEpisode]) -> set[int]:
    """原著章节在剧本写完之后被改过 / 删掉了的分集 id"""
    if not rows:
        return set()
    chs = {c.chapter_no: c.updated_at for c in db.query(Chapter.chapter_no, Chapter.updated_at)
           .filter(Chapter.novel_id == novel_id, Chapter.status.in_(CHAPTER_WRITTEN_STATUSES))}
    changed = set()
    for ep in rows:
        ref = ep.script_at or ep.created_at
        for no in range(ep.chapter_from, ep.chapter_to + 1):
            if no not in chs or (ref and chs[no] and chs[no] > ref):
                changed.add(ep.id)
                break
    return changed
