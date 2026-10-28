"""后台操作记录：自动记录 /api/admin 下所有"会改动数据"的请求（谁、什么时候、做了什么、成没成功）

以 ASGI 中间件实现，不改各个接口；动作名按路径匹配成中文，删除/修改前先查出目标的名字，删掉之后也能看出删的是什么。
"""
import asyncio
import json
import re

from app.helpers import now

# (方法, 路径正则, 动作, 目标类型) —— 按顺序匹配第一个
ROUTES: list[tuple[str, str, str, str]] = [
    ("POST", r"/login", "登录", ""),
    ("POST", r"/logout", "退出登录", ""),
    ("PUT", r"/password", "修改密码", ""),
    ("POST", r"/novels", "新建小说", "novel"),
    ("PUT", r"/novels/(\d+)", "修改小说信息", "novel"),
    ("DELETE", r"/novels/(\d+)", "删除小说（移入回收站）", "novel"),
    ("POST", r"/novels/(\d+)/restore", "从回收站恢复小说", "novel"),
    ("DELETE", r"/novels/(\d+)/purge", "彻底删除小说", "novel"),
    ("PUT", r"/novels/(\d+)/setting", "修改设定", "novel"),
    ("PUT", r"/novels/(\d+)/memory", "修改记忆", "novel"),
    ("PUT", r"/novels/(\d+)/outline", "修改大纲", "novel"),
    ("PUT", r"/novels/(\d+)/automation", "修改自动化设置", "novel"),
    ("POST", r"/novels/(\d+)/consistency", "运行一致性审校", "novel"),
    ("POST", r"/novels/(\d+)/chapters/publish", "发布草稿章节", "novel"),
    ("POST", r"/novels/(\d+)/chapters", "新增章节", "novel"),
    ("PUT", r"/chapters/(\d+)", "编辑章节", "chapter"),
    ("DELETE", r"/chapters/(\d+)", "删除章节", "chapter"),
    ("POST", r"/chapter-versions/(\d+)/restore", "恢复章节历史版本", "chapter_version"),
    ("POST", r"/ai/tasks", "创建 AI 任务", "novel"),
    ("POST", r"/ai/tasks/(\d+)/cancel", "取消 AI 任务", "task"),
    ("POST", r"/ai/tasks/(\d+)/retry", "重试 AI 任务", "task"),
    ("POST", r"/ideas", "新建点子", "idea"),
    ("PUT", r"/ideas/(\d+)", "修改点子", "idea"),
    ("DELETE", r"/ideas/(\d+)", "删除点子", "idea"),
    ("POST", r"/ideas/(\d+)/chat", "点子聊天", "idea"),
    ("POST", r"/ideas/(\d+)/save", "AI 整理点子", "idea"),
    ("POST", r"/ideas/(\d+)/create-novel", "点子转为小说", "idea"),
    ("POST", r"/categories", "新建分类", "category"),
    ("PUT", r"/categories/(\d+)", "修改分类", "category"),
    ("DELETE", r"/categories/(\d+)", "删除分类", "category"),
    ("POST", r"/ai/providers", "新增 AI 服务商", "provider"),
    ("PUT", r"/ai/providers/(\d+)", "修改 AI 服务商", "provider"),
    ("DELETE", r"/ai/providers/(\d+)", "删除 AI 服务商", "provider"),
    ("POST", r"/ai/providers/(\d+)/default", "设为默认服务商", "provider"),
    ("POST", r"/ai/models", "新增模型", "model"),
    ("PUT", r"/ai/models/(\d+)", "修改模型", "model"),
    ("DELETE", r"/ai/models/(\d+)", "删除模型", "model"),
    ("POST", r"/ai/models/(\d+)/default", "设为默认模型", "model"),
    ("PUT", r"/prompts/(\d+)", "修改 Prompt", "prompt"),
    ("DELETE", r"/ai/logs", "清空 AI 日志", ""),
    ("PUT", r"/config", "修改系统设置", ""),
]
_COMPILED = [(m, re.compile(r"^/api/admin" + p + r"/?$"), a, t) for m, p, a, t in ROUTES]
# 不记录：只读请求，以及纯展示用的高频请求
SKIP_METHODS = {"GET", "HEAD", "OPTIONS"}
BODY_CAPTURE_LIMIT = 4096


def match(method: str, path: str) -> tuple[str, str, int]:
    for m, regex, action, target_type in _COMPILED:
        if m == method:
            found = regex.match(path)
            if found:
                target_id = int(found.group(1)) if found.groups() else 0
                return action, target_type, target_id
    return f"{method} {path.removeprefix('/api/admin')}", "", 0


def describe_target(target_type: str, target_id: int) -> str:
    """查出目标的可读名字（在请求处理前调用，删除后也能知道删的是谁）"""
    if not target_type or not target_id:
        return ""
    from app.db import SessionLocal
    from app.models import AiModel, AiProvider, AiTask, Category, Chapter, ChapterVersion, Idea, Novel
    db = SessionLocal()
    try:
        if target_type == "novel":
            n = db.get(Novel, target_id)
            return f"《{n.title}》" if n else f"小说 #{target_id}"
        if target_type == "chapter":
            c = db.get(Chapter, target_id)
            if c:
                n = db.get(Novel, c.novel_id)
                return f"《{n.title if n else c.novel_id}》第{c.chapter_no}章 {c.title}"
        if target_type == "chapter_version":
            v = db.get(ChapterVersion, target_id)
            if v:
                n = db.get(Novel, v.novel_id)
                return f"《{n.title if n else v.novel_id}》第{v.chapter_no}章（{v.created_at:%m-%d %H:%M} 的版本）"
        if target_type == "task":
            t = db.get(AiTask, target_id)
            if t:
                n = db.get(Novel, t.ref_id)
                return f"任务 #{t.id} {t.type_text}" + (f"（《{n.title}》）" if n else "")
        names = {"idea": (Idea, "title"), "category": (Category, "name"),
                 "provider": (AiProvider, "name"), "model": (AiModel, "name")}
        if target_type in names:
            model, attr = names[target_type]
            row = db.get(model, target_id)
            if row:
                return str(getattr(row, attr))
        return f"#{target_id}"
    except Exception:  # noqa: BLE001  记录失败不能影响业务
        return f"#{target_id}"
    finally:
        db.close()


def write(entry: dict) -> None:
    from app.db import SessionLocal
    from app.models import Admin, OperationLog
    db = SessionLocal()
    try:
        if entry.get("admin_id") and not entry.get("username"):
            admin = db.get(Admin, entry["admin_id"])
            entry["username"] = admin.username if admin else ""
        db.add(OperationLog(created_at=now(), **entry))
        db.commit()
    except Exception as e:  # noqa: BLE001
        print("[audit] 写操作记录失败:", e)
    finally:
        db.close()


def _client_ip(scope) -> str:
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
    forwarded = headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return headers.get("x-real-ip") or (scope.get("client") or ["unknown"])[0]


class AuditMiddleware:
    """必须放在 SessionMiddleware 内层（先 add），这样能读到 scope["session"] 里的登录信息"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in SKIP_METHODS \
                or not scope["path"].startswith("/api/admin"):
            await self.app(scope, receive, send)
            return

        method, path = scope["method"], scope["path"]
        action, target_type, target_id = match(method, path)
        target = await asyncio.to_thread(describe_target, target_type, target_id) if target_id else ""

        # 登录请求：只取用户名（绝不记录密码）
        request_body = bytearray()
        capture_request = path.rstrip("/").endswith("/login")

        async def receive_wrapper():
            message = await receive()
            if capture_request and message.get("type") == "http.request" and len(request_body) < BODY_CAPTURE_LIMIT:
                request_body.extend(message.get("body", b"")[:BODY_CAPTURE_LIMIT])
            return message

        state = {"status": 0, "json": False, "body": bytearray()}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                content_type = dict(message.get("headers") or []).get(b"content-type", b"")
                state["json"] = b"application/json" in content_type
            elif message["type"] == "http.response.body" and state["json"] \
                    and len(state["body"]) < BODY_CAPTURE_LIMIT:
                state["body"].extend(message.get("body", b"")[:BODY_CAPTURE_LIMIT])
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            success, message = state["status"] < 400, ""
            if state["json"]:
                try:
                    payload = json.loads(bytes(state["body"]))
                    if isinstance(payload, dict) and payload.get("code", 0) != 0:
                        success, message = False, str(payload.get("msg", ""))[:255]
                    elif isinstance(payload, dict) and action == "创建 AI 任务":
                        data = payload.get("data") or {}
                        action = f"创建 AI 任务：{data.get('task_type_text') or data.get('task_type', '')}"
                        target_id = int(data.get("ref_id") or 0)
                        target_type = "novel"
                except ValueError:
                    pass
            elif state["status"] >= 400:
                message = f"HTTP {state['status']}"
            session = scope.get("session") or {}
            username = ""
            if capture_request:
                try:
                    username = str(json.loads(bytes(request_body)).get("username", ""))[:64]
                except (ValueError, AttributeError):
                    pass
            if action.startswith("创建 AI 任务") and target_id and not target:
                target = await asyncio.to_thread(describe_target, "novel", target_id)
            entry = {
                "admin_id": int(session.get("admin_id") or 0), "username": username,
                "action": action[:64], "summary": target[:500], "method": method, "path": path[:255],
                "target_type": target_type, "target_id": target_id, "success": 1 if success else 0,
                "message": message, "ip": _client_ip(scope)[:64],
            }
            await asyncio.to_thread(write, entry)
