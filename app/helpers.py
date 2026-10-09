"""通用辅助：响应包装 / 时间 / 字数统计（与 PHP 版契约一致）"""
import json
import re
from datetime import datetime
from typing import Any

from fastapi.responses import JSONResponse


def now() -> datetime:
    return datetime.now()


def dt_str(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def word_count(text: str | None) -> int:
    if not text:
        return 0
    return len(re.sub(r"\s+", "", text))


def ok(data: Any = None, msg: str = "ok", http_status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=http_status, content={"code": 0, "msg": msg, "data": data})


def fail(msg: str = "error", code: int = 1, data: Any = None, http_status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=http_status, content={"code": code, "msg": msg, "data": data})


def fail_401(msg: str = "未登录或登录已过期") -> JSONResponse:
    """HTTP 401 + body code 401（前端据此跳转登录页）"""
    return JSONResponse(status_code=401, content={"code": 401, "msg": msg, "data": None})


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
