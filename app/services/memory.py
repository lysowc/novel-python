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
]


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
            planted = int(f.get("planted_chapter") or 0)
            resolved = int(f.get("resolved_chapter") or 0)
            suffix = (
                f"（已回收，第{resolved}章）" if status == "resolved"
                else (f"（约第{planted}章埋下）" if planted > 0 else "")
            )
            lines.append(f"- {desc}{suffix}")
        return "\n".join(lines)
    if slot in ("world_facts", "unresolved_events"):
        lines = []
        for fact in value if isinstance(value, list) else []:
            if isinstance(fact, dict):
                fact = fact.get("description") or fact.get("name") or ""
            fact = str(fact).strip()
            if fact:
                lines.append("- " + fact)
        return "\n".join(lines)
    if slot == "timeline":
        lines = []
        for event in value if isinstance(value, list) else []:
            if not isinstance(event, dict):
                continue
            chapter = int(event.get("chapter") or 0)
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


def to_context_text(content: str | None) -> str:
    """记忆 → AI 上下文文本（v2 结构化渲染 / 旧格式兼容）"""
    data = parse_memory(content)
    if not data:
        return "（暂无小说记忆）"
    if data.get("schema") == SCHEMA_V2:
        return render_structured(data)
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
            chars.append({"name": c.strip(), "status": "", "relationships": "", "goals": ""})
        elif isinstance(c, dict):
            chars.append({
                "name": str(c.get("name", "")).strip(),
                "status": str(c.get("status", "")).strip(),
                "relationships": str(c.get("relationships", "")).strip(),
                "goals": str(c.get("goals", "")).strip(),
            })
    out["characters"] = [c for c in chars if c["name"]]

    fores = []
    for f in data.get("foreshadowing", []) if isinstance(data.get("foreshadowing"), list) else []:
        if isinstance(f, str):
            fores.append({"description": f.strip(), "planted_chapter": 0, "status": "open", "resolved_chapter": 0})
        elif isinstance(f, dict):
            status = str(f.get("status", "open"))
            fores.append({
                "description": str(f.get("description") or f.get("name") or "").strip(),
                "planted_chapter": int(f.get("planted_chapter") or 0),
                "status": status if status in ("open", "resolved") else "open",
                "resolved_chapter": int(f.get("resolved_chapter") or 0),
            })
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
                "chapter": int(event.get("chapter") or 0),
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

    return out
