"""结构化记忆 v2：解析 / 渲染 / 规整（与 PHP 版 NovelMemory + normalizeMemory 对齐）"""
import json

SCHEMA_V2 = "v2"

SLOTS = [
    ("current_state", "当前状态"),
    ("characters", "人物状态"),
    ("foreshadowing", "未回收伏笔"),
    ("world_facts", "世界观增量"),
    ("timeline", "关键时间线"),
    ("unresolved_events", "未解决事件"),
    ("important_items", "重要物品"),
    ("style_notes", "文风备注"),
    ("character_archive", "其他已登场人物"),
]

ARCHIVE_LIMIT = 200      # 已登场人物名册上限
WORLD_FACTS_LIMIT = 1000  # 世界观事实存储上限（只防异常膨胀；写章节时按相关性挑着带，见 select_for_context）
ACTIVE_CHAR_LIMIT = 25   # 「人物状态」里的近期活跃人物上限（超出的按最久未出场移入名册）
TIMELINE_LIMIT = 40      # 时间线只留最近的（更早的剧情由分段总结覆盖）
UNRESOLVED_LIMIT = 40
FORESHADOW_LIMIT = 80     # 只限制「带进写章节上下文」的条数；未回收的伏笔永远不删
RESOLVED_KEEP = 20        # 已回收的伏笔只留最近这么多条（提醒"别再回收一次"）

# 写章节时带进上下文的条数：主线伏笔全部带；次要伏笔和世界观带"最近的 + 和本章相关的"
FORESHADOW_CONTEXT_RECENT = 30
FORESHADOW_CONTEXT_RELEVANT = 15
FACTS_CONTEXT_RECENT = 40
FACTS_CONTEXT_RELEVANT = 20


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_memory(content: str | None) -> dict:
    if not content:
        return {}
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("schema") == SCHEMA_V2:
        return data
    if isinstance(data, list):
        legacy = {}
        for item in data:
            if isinstance(item, dict) and "key" in item and "value" in item:
                legacy[str(item["key"])] = str(item["value"])
        return {"schema": "legacy_pairs", "items": legacy}
    return {"schema": "legacy_object", "items": data}


def is_structured(content: str | None) -> bool:
    return parse_memory(content).get("schema") == SCHEMA_V2


def _render_slot(slot: str, value) -> str:
    if value is None or value == "" or value == []:
        return ""
    if slot == "current_state":
        if not isinstance(value, dict):
            return str(value)
        lines = []
        if str(value.get("location", "")).strip():
            lines.append("地点：" + str(value["location"]))
        if str(value.get("time", "")).strip():
            lines.append("时间：" + str(value["time"]))
        if str(value.get("plot_progress", "")).strip():
            lines.append("剧情进展：" + str(value["plot_progress"]))
        return "\n".join(lines)
    if slot == "characters":
        lines = []
        for c in value if isinstance(value, list) else []:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name", "")).strip()
            if not name:
                continue
            bits = [name]
            if str(c.get("status", "")).strip():
                bits.append("状态：" + str(c["status"]))
            if str(c.get("relationships", "")).strip():
                bits.append("关系：" + str(c["relationships"]))
            if str(c.get("goals", "")).strip():
                bits.append("目标：" + str(c["goals"]))
            lines.append("- " + "；".join(bits))
        return "\n".join(lines)
    if slot == "foreshadowing":
        lines = []
        for f in value if isinstance(value, list) else []:
            if not isinstance(f, dict):
                continue
            desc = str(f.get("description", "")).strip()
            if not desc:
                continue
            status = str(f.get("status", "open"))
            planted = _int(f.get("planted_chapter"))
            resolved = _int(f.get("resolved_chapter"))
            suffix = (
                f"（已回收，第{resolved}章）" if status == "resolved"
                else (f"（约第{planted}章埋下）" if planted > 0 else "")
            )
            tag = "【主线】" if f.get("major") and status != "resolved" else ""
            lines.append(f"- {tag}{desc}{suffix}")
        if isinstance(value, list) and value and isinstance(value[-1], dict) and value[-1].get("_omitted"):
            lines.append(f"（另有 {value[-1]['_omitted']} 条较早的次要伏笔与本章关系不大，未列出）")
        return "\n".join(lines)
    if slot in ("world_facts", "unresolved_events"):
        lines = []
        omitted = 0
        for fact in value if isinstance(value, list) else []:
            if isinstance(fact, dict):
                if fact.get("_omitted"):
                    omitted = int(fact["_omitted"])
                    continue
                fact = fact.get("description") or fact.get("name") or ""
            fact = str(fact).strip()
            if fact:
                lines.append("- " + fact)
        if omitted:
            lines.append(f"（另有 {omitted} 条较早的设定与本章关系不大，未列出）")
        return "\n".join(lines)
    if slot == "timeline":
        lines = []
        for event in value if isinstance(value, list) else []:
            if not isinstance(event, dict):
                continue
            chapter = _int(event.get("chapter"))
            desc = str(event.get("event", "")).strip()
            if not desc:
                continue
            lines.append(f"- 第{chapter}章：{desc}" if chapter > 0 else f"- {desc}")
        return "\n".join(lines)
    if slot == "important_items":
        lines = []
        for item in value if isinstance(value, list) else []:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                status = str(item.get("status", "")).strip()
                lines.append(f"- {name}" + (f"（{status}）" if status else ""))
            else:
                lines.append("- " + str(item))
        return "\n".join(lines)
    if slot == "character_archive":
        bits = []
        for c in value if isinstance(value, list) else []:
            if isinstance(c, dict) and str(c.get("name", "")).strip():
                status = str(c.get("status", "")).strip()
                bits.append(c["name"].strip() + (f"（{status[:20]}）" if status else ""))
        return "、".join(bits)
    # style_notes 等纯文本槽
    if isinstance(value, list):
        return "\n".join(str(x) for x in value)
    return str(value)


def render_structured(data: dict) -> str:
    parts = []
    for slot, label in SLOTS:
        content = _render_slot(slot, data.get(slot))
        if content:
            parts.append(f"【{label}】\n{content}")
    return "\n\n".join(parts) if parts else "（暂无结构化记忆）"


def _relevance(text: str, hint_grams: set[str]) -> float:
    grams = _bigrams(text)
    return len(grams & hint_grams) / len(grams) if grams and hint_grams else 0.0


def _pick(items: list, recent: int, relevant: int, hint_grams: set[str], key=lambda x: x) -> tuple[list, int]:
    """保留最近 recent 条 + 与 hint 最相关的 relevant 条（保持原顺序），返回 (挑出的, 省略条数)"""
    if len(items) <= recent + relevant:
        return items, 0
    keep = set(range(len(items) - recent, len(items)))
    scored = sorted(((_relevance(key(x), hint_grams), i) for i, x in enumerate(items) if i not in keep), reverse=True)
    keep |= {i for sc, i in scored[:relevant] if sc >= 0.25}
    picked = [x for i, x in enumerate(items) if i in keep]
    return picked, len(items) - len(picked)


def select_for_context(data: dict, hint: str) -> dict:
    """写章节用的记忆视图：主线伏笔全部保留；次要伏笔、世界观只带最近的和与本章（大纲/上一章结尾）相关的。
    记忆本身一条不删，只是不全塞给 AI——几百章后也不会把上下文撑爆。"""
    out = dict(data)
    grams = _bigrams(hint or "")
    fores = [f for f in data.get("foreshadowing", []) if isinstance(f, dict)]
    opened = [f for f in fores if f.get("status") != "resolved"]
    major = [f for f in opened if f.get("major")]
    minor = [f for f in opened if not f.get("major")]
    minor_picked, omitted = _pick(minor, FORESHADOW_CONTEXT_RECENT, FORESHADOW_CONTEXT_RELEVANT, grams,
                                  key=lambda f: f.get("description", ""))
    resolved = [f for f in fores if f.get("status") == "resolved"][-10:]
    out["foreshadowing"] = major + minor_picked + resolved + ([{"_omitted": omitted}] if omitted else [])
    facts = [f for f in data.get("world_facts", []) if f]
    facts_picked, f_omitted = _pick(facts, FACTS_CONTEXT_RECENT, FACTS_CONTEXT_RELEVANT, grams, key=str)
    out["world_facts"] = facts_picked + ([{"_omitted": f_omitted}] if f_omitted else [])
    return out


def to_context_text(content: str | None, hint: str | None = None) -> str:
    """记忆 → AI 上下文文本（v2 结构化渲染 / 旧格式兼容）。
    给了 hint（本章大纲、上一章结尾等）时，次要伏笔和世界观按相关性挑选，见 select_for_context。"""
    data = parse_memory(content)
    if not data:
        return "（暂无小说记忆）"
    if data.get("schema") == SCHEMA_V2:
        return render_structured(select_for_context(data, hint) if hint is not None else data)
    if data.get("schema") == "legacy_pairs":
        lines = [f"- {k}：{v}" for k, v in data.get("items", {}).items()]
        return "\n".join(lines) if lines else "（暂无小说记忆）"
    lines = []
    for key, value in data.get("items", {}).items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"- {key}：" + str(value))
    return "\n".join(lines) if lines else "（暂无小说记忆）"


def normalize_memory(data: dict) -> dict:
    """把 AI 返回的记忆数据规整为 v2 结构（缺槽补默认值、条目形状归一、旧字段迁移）"""
    out: dict = {"schema": SCHEMA_V2}

    cs = data.get("current_state") if isinstance(data.get("current_state"), dict) else {}
    out["current_state"] = {
        "location": str(cs.get("location") or data.get("current_location") or "").strip(),
        "time": str(cs.get("time") or data.get("current_time") or "").strip(),
        "plot_progress": str(cs.get("plot_progress") or data.get("main_plot") or "").strip(),
    }

    raw_chars = data.get("characters")
    if not isinstance(raw_chars, list) and isinstance(data.get("main_character"), dict):
        raw_chars = [data["main_character"]]
    chars = []
    for c in raw_chars if isinstance(raw_chars, list) else []:
        if isinstance(c, str):
            chars.append({"name": c.strip(), "status": "", "relationships": "", "goals": "", "last_chapter": 0})
        elif isinstance(c, dict):
            chars.append({
                "name": str(c.get("name", "")).strip(),
                "status": str(c.get("status", "")).strip(),
                "relationships": str(c.get("relationships", "")).strip(),
                "goals": str(c.get("goals", "")).strip(),
                "last_chapter": _int(c.get("last_chapter")),
            })
    out["characters"] = [c for c in chars if c["name"]]

    fores = []
    for f in data.get("foreshadowing", []) if isinstance(data.get("foreshadowing"), list) else []:
        if isinstance(f, str):
            fores.append({"description": f.strip(), "planted_chapter": 0, "status": "open", "resolved_chapter": 0})
        elif isinstance(f, dict):
            status = str(f.get("status", "open"))
            item = {
                "description": str(f.get("description") or f.get("name") or "").strip(),
                "planted_chapter": _int(f.get("planted_chapter")),
                "status": status if status in ("open", "resolved") else "open",
                "resolved_chapter": _int(f.get("resolved_chapter")),
            }
            if f.get("major"):
                item["major"] = 1
            fores.append(item)
    out["foreshadowing"] = [f for f in fores if f["description"]]

    def string_list(slot: str) -> list[str]:
        items = []
        for item in data.get(slot, []) if isinstance(data.get(slot), list) else []:
            if isinstance(item, dict):
                item = item.get("description") or item.get("name") or item.get("event") or ""
            item = str(item).strip()
            if item:
                items.append(item)
        return items

    out["world_facts"] = string_list("world_facts")
    out["unresolved_events"] = string_list("unresolved_events")

    timeline = []
    for event in data.get("timeline", []) if isinstance(data.get("timeline"), list) else []:
        if isinstance(event, str):
            timeline.append({"chapter": 0, "event": event.strip()})
        elif isinstance(event, dict):
            timeline.append({
                "chapter": _int(event.get("chapter")),
                "event": str(event.get("event", "")).strip(),
            })
    out["timeline"] = [e for e in timeline if e["event"]]

    items = []
    for item in data.get("important_items", []) if isinstance(data.get("important_items"), list) else []:
        if isinstance(item, str):
            items.append({"name": item.strip(), "status": ""})
        elif isinstance(item, dict):
            items.append({
                "name": str(item.get("name", "")).strip(),
                "status": str(item.get("status", "")).strip(),
            })
    out["important_items"] = [i for i in items if i["name"]]

    style = data.get("style_notes", "")
    out["style_notes"] = "\n".join(str(x) for x in style) if isinstance(style, list) else str(style).strip()

    archive = []
    for c in data.get("character_archive", []) if isinstance(data.get("character_archive"), list) else []:
        if isinstance(c, str) and c.strip():
            archive.append({"name": c.strip(), "status": ""})
        elif isinstance(c, dict) and str(c.get("name", "")).strip():
            archive.append({"name": str(c["name"]).strip(), "status": str(c.get("status", "")).strip()})
    out["character_archive"] = archive

    return out


# ---------- 增量合并：防止 AI 重写记忆时"顺手"丢掉信息 ----------

def _bigrams(text: str) -> set[str]:
    t = "".join(str(text).split()).lower()
    return {t[i:i + 2] for i in range(len(t) - 1)} or ({t} if t else set())


def _similar(a: str, b: str, threshold: float = 0.5) -> bool:
    """字符二元组重叠系数（交集 / 较短一方）：AI 换个说法、加几个字描述同一条伏笔/事实时也能认出来"""
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return False
    return len(x & y) / min(len(x), len(y)) >= threshold


def merge_memory(old_content: str | None, new: dict) -> tuple[dict, list[str]]:
    """把 AI 新输出的记忆（已 normalize）与旧记忆合并，返回 (合并结果, 找回的条目说明)。

    记忆每章都由 AI 整份重写，容易漏掉信息，这里兜底：
    - 未回收（open）的伏笔：新记忆里没有、也没被标为已回收 → 补回（伏笔只能被"回收"，不能被"遗忘"）
    - 重要物品、世界观事实：新记忆里没有 → 补回
    - 人物：新记忆里删掉的人物移入「其他已登场人物」名册，保留姓名和最后状态，避免后文写出同名新角色或"复活"
    - 当前状态的字段：新值为空时沿用旧值
    - 时间线、未解决事件按 prompt 本来就允许删减，不补回
    """
    old = parse_memory(old_content)
    if old.get("schema") != SCHEMA_V2:
        return new, []
    old = normalize_memory(old)
    out = json.loads(json.dumps(new, ensure_ascii=False))
    restored: list[str] = []

    cs_old, cs_new = old.get("current_state") or {}, out.setdefault("current_state", {})
    for k in ("location", "time", "plot_progress"):
        if not str(cs_new.get(k, "")).strip() and str(cs_old.get(k, "")).strip():
            cs_new[k] = cs_old[k]

    new_fores = out.setdefault("foreshadowing", [])
    for f in old.get("foreshadowing", []):
        if f.get("status") != "open":
            continue
        if not any(_similar(f["description"], n["description"]) for n in new_fores):
            new_fores.append(f)
            restored.append("伏笔：" + f["description"][:30])

    new_items = out.setdefault("important_items", [])
    names = {i["name"] for i in new_items}
    for item in old.get("important_items", []):
        if item["name"] not in names:
            new_items.append(item)
            names.add(item["name"])
            restored.append("物品：" + item["name"])

    new_facts = out.setdefault("world_facts", [])
    for fact in old.get("world_facts", []):
        if not any(_similar(fact, n) for n in new_facts):
            new_facts.append(fact)
            restored.append("世界观：" + fact[:30])
    out["world_facts"] = new_facts[-WORLD_FACTS_LIMIT:]

    active = {c["name"] for c in out.get("characters", [])}
    archive = {c["name"]: c for c in old.get("character_archive", [])}
    for c in new.get("character_archive", []) or []:
        archive[c["name"]] = c
    for c in old.get("characters", []):
        if c["name"] not in active:
            archive[c["name"]] = {"name": c["name"], "status": c.get("status", "")}
    for name in active:  # 重新活跃的人物移出名册
        archive.pop(name, None)
    out["character_archive"] = list(archive.values())[-ARCHIVE_LIMIT:]

    return out, restored


# ---------- 增量补丁：AI 只输出"本章带来的变化"，不再整份重写 ----------

PATCH_KEYS = (
    "current_state", "characters_upsert", "characters_left", "foreshadowing_add", "foreshadowing_resolve",
    "foreshadowing_major", "world_facts_add", "timeline_add", "unresolved_add", "unresolved_resolve", "items_upsert",
    "style_notes", "profiles", "terms",
)


def is_patch(data: dict) -> bool:
    """AI 返回的是补丁格式（而不是整份记忆）"""
    return isinstance(data, dict) and any(k in data for k in PATCH_KEYS if k != "current_state") \
        and data.get("schema") != SCHEMA_V2


def _str_list(value) -> list[str]:
    out = []
    for item in value if isinstance(value, list) else ([value] if isinstance(value, str) else []):
        if isinstance(item, dict):
            item = item.get("description") or item.get("name") or item.get("event") or ""
        item = str(item).strip()
        if item:
            out.append(item)
    return out


def _best_match(text: str, candidates: list[str], threshold: float = 0.5) -> int:
    """在 candidates 中找与 text 最相近的一条（包含关系或二元组重叠），返回下标；没有返回 -1"""
    best, best_score = -1, 0.0
    t = "".join(text.split())
    x = _bigrams(text)
    for i, c in enumerate(candidates):
        cc = "".join(c.split())
        if t and (t in cc or cc in t):
            return i
        y = _bigrams(c)
        if not x or not y:
            continue
        score = len(x & y) / min(len(x), len(y))
        if score >= threshold and score > best_score:
            best, best_score = i, score
    return best


def apply_patch(old_content: str | None, patch: dict, chapter_no: int) -> tuple[dict, list[str]]:
    """把第 chapter_no 章的记忆补丁应用到旧记忆上，返回 (新记忆, 变更说明)。

    旧记忆里没被补丁提到的内容原样保留——不会再出现"AI 重写时顺手丢掉伏笔"的问题。
    """
    old = parse_memory(old_content)
    base = normalize_memory(old if old.get("schema") == SCHEMA_V2 else {})
    out = json.loads(json.dumps(base, ensure_ascii=False))
    notes: list[str] = []

    # 当前状态：给了哪个字段就更新哪个
    cs = patch.get("current_state") if isinstance(patch.get("current_state"), dict) else {}
    for k in ("location", "time", "plot_progress"):
        v = str(cs.get(k) or "").strip()
        if v:
            out["current_state"][k] = v
    story_time = str(patch.get("story_time") or "").strip()
    if story_time:
        out["current_state"]["time"] = story_time

    # 人物：按姓名更新/新增；离场的移入名册
    chars = out["characters"]
    by_name = {c["name"]: c for c in chars}
    for c in patch.get("characters_upsert") or []:
        if isinstance(c, str):
            c = {"name": c}
        if not isinstance(c, dict) or not str(c.get("name", "")).strip():
            continue
        name = str(c["name"]).strip()
        row = by_name.get(name)
        if row is None:
            row = {"name": name, "status": "", "relationships": "", "goals": "", "last_chapter": chapter_no}
            chars.append(row)
            by_name[name] = row
            notes.append("新人物：" + name)
        for k in ("status", "relationships", "goals"):
            v = str(c.get(k) or "").strip()
            if v:
                row[k] = v
        row["last_chapter"] = chapter_no
    archive = {a["name"]: a for a in out.get("character_archive", [])}
    for name in archive.keys() & by_name.keys():  # 重新登场的移出名册
        archive.pop(name, None)
    left = set(_str_list(patch.get("characters_left")))
    for name in left:
        row = by_name.pop(name, None)
        if row:
            archive[name] = {"name": name, "status": row.get("status", "")}
    active = [c for c in chars if c["name"] in by_name]
    if len(active) > ACTIVE_CHAR_LIMIT:
        active.sort(key=lambda c: c.get("last_chapter") or 0, reverse=True)
        for c in active[ACTIVE_CHAR_LIMIT:]:
            archive[c["name"]] = {"name": c["name"], "status": c.get("status", "")}
        keep = {c["name"] for c in active[:ACTIVE_CHAR_LIMIT]}
        active = [c for c in chars if c["name"] in keep]
    out["characters"] = active
    out["character_archive"] = list(archive.values())[-ARCHIVE_LIMIT:]

    # 伏笔：新增 / 回收（模糊匹配描述）
    fores = out["foreshadowing"]
    for desc in _str_list(patch.get("foreshadowing_resolve")):
        open_list = [f for f in fores if f["status"] == "open"]
        i = _best_match(desc, [f["description"] for f in open_list])
        if i >= 0:
            open_list[i]["status"] = "resolved"
            open_list[i]["resolved_chapter"] = chapter_no
            notes.append("回收伏笔：" + open_list[i]["description"][:30])
    for raw in patch.get("foreshadowing_add") or []:
        # 可以是字符串，也可以是 {"description": ..., "major": true}（主线伏笔，写每章都会带上）
        if isinstance(raw, dict):
            desc = str(raw.get("description") or raw.get("name") or "").strip()
            major = bool(raw.get("major"))
        else:
            desc, major = str(raw or "").strip(), False
        if not desc:
            continue
        open_list = [f for f in fores if f["status"] == "open"]
        i = _best_match(desc, [f["description"] for f in open_list], 0.7)
        if i < 0:
            item = {"description": desc, "planted_chapter": chapter_no, "status": "open", "resolved_chapter": 0}
            if major:
                item["major"] = 1
            fores.append(item)
            notes.append(("新主线伏笔：" if major else "新伏笔：") + desc[:30])
        elif major:
            open_list[i]["major"] = 1
    for desc in _str_list(patch.get("foreshadowing_major")):
        # 把已有的伏笔升级为主线
        open_list = [f for f in fores if f["status"] == "open"]
        i = _best_match(desc, [f["description"] for f in open_list])
        if i >= 0:
            open_list[i]["major"] = 1
    # 未回收的伏笔一条都不删（以前超过上限会把最早的删掉，主线伏笔就这么被忘了）；
    # 已回收的只留最近几条，够提醒"别再回收一次"
    resolved = [f for f in fores if f["status"] == "resolved"][-RESOLVED_KEEP:]
    opened = [f for f in fores if f["status"] == "open"]
    out["foreshadowing"] = opened + resolved

    for fact in _str_list(patch.get("world_facts_add")):
        if not any(_similar(fact, n) for n in out["world_facts"]):
            out["world_facts"].append(fact)
    out["world_facts"] = out["world_facts"][-WORLD_FACTS_LIMIT:]

    for event in patch.get("timeline_add") or []:
        desc = str(event.get("event") if isinstance(event, dict) else event or "").strip()
        if desc:
            out["timeline"].append({"chapter": chapter_no, "event": desc})
    out["timeline"] = out["timeline"][-TIMELINE_LIMIT:]

    unresolved = out["unresolved_events"]
    for desc in _str_list(patch.get("unresolved_resolve")):
        i = _best_match(desc, unresolved)
        if i >= 0:
            unresolved.pop(i)
    for desc in _str_list(patch.get("unresolved_add")):
        if _best_match(desc, unresolved, 0.7) < 0:
            unresolved.append(desc)
    out["unresolved_events"] = unresolved[-UNRESOLVED_LIMIT:]

    items = {i["name"]: i for i in out["important_items"]}
    for item in patch.get("items_upsert") or []:
        if isinstance(item, str):
            item = {"name": item}
        if isinstance(item, dict) and str(item.get("name", "")).strip():
            name = str(item["name"]).strip()
            status = str(item.get("status") or "").strip()
            if name in items:
                if status:
                    items[name]["status"] = status
            else:
                items[name] = {"name": name, "status": status}
    out["important_items"] = list(items.values())

    style = patch.get("style_notes")
    if isinstance(style, str) and style.strip():
        out["style_notes"] = style.strip()

    return out, notes
