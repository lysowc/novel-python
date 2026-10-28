"""登录限流与安全配置检查"""
import threading
import time

from fastapi import Request

from app.config import settings

LOGIN_WINDOW = 600            # 统计窗口（秒）
MAX_FAILS_PER_IP_USER = 5     # 同一 IP + 账号 10 分钟内最多失败 5 次
MAX_FAILS_PER_USER = 20       # 同一账号（不论 IP）10 分钟内最多失败 20 次，防换 IP 爆破

WEAK_SECRETS = {"", "change-me", "novel-python-secret-change-me", "secret", "changeme"}

_memory_fails: dict[str, tuple[int, float]] = {}  # Redis 不可用时的进程内兜底
_lock = threading.Lock()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def _keys(request: Request, username: str) -> list[tuple[str, int]]:
    user = username.lower()[:64]
    return [(f"login:fail:{client_ip(request)}:{user}", MAX_FAILS_PER_IP_USER),
            (f"login:fail:user:{user}", MAX_FAILS_PER_USER)]


def _redis():
    from app.services.task_service import connect_redis
    return connect_redis()


def login_blocked(request: Request, username: str) -> bool:
    keys = _keys(request, username)
    try:
        r = _redis()
        try:
            values = r.mget([k for k, _ in keys])
        finally:
            r.close()
        return any(int(v or 0) >= limit for v, (_, limit) in zip(values, keys))
    except Exception:
        now_ts = time.time()
        with _lock:
            return any(
                (rec := _memory_fails.get(k)) and rec[1] > now_ts and rec[0] >= limit
                for k, limit in keys
            )


def record_failure(request: Request, username: str) -> None:
    keys = _keys(request, username)
    try:
        r = _redis()
        try:
            # 不用 EXPIRE ... NX（Redis 7 才支持，Ubuntu 22.04 自带的是 Redis 6）：
            # 先 SET NX 建一个带过期时间的计数器，再 INCR，所有版本都支持
            pipe = r.pipeline()
            for k, _ in keys:
                pipe.set(k, 0, ex=LOGIN_WINDOW, nx=True)
                pipe.incr(k)
            pipe.execute()
        finally:
            r.close()
    except Exception:
        now_ts = time.time()
        with _lock:
            for k, _ in keys:
                count, expires = _memory_fails.get(k, (0, 0))
                if expires <= now_ts:
                    count, expires = 0, now_ts + LOGIN_WINDOW
                _memory_fails[k] = (count + 1, expires)


def clear_failures(request: Request, username: str) -> None:
    keys = [k for k, _ in _keys(request, username)][:1]  # 成功登录只清本 IP 的计数
    try:
        r = _redis()
        try:
            r.delete(*keys)
        finally:
            r.close()
    except Exception:
        with _lock:
            for k in keys:
                _memory_fails.pop(k, None)


def config_warnings(admin_password_hash: str | None = None) -> list[str]:
    """需要提醒管理员处理的安全配置"""
    warnings = []
    secret = settings.session_secret or ""
    if secret.strip().lower() in WEAK_SECRETS or len(secret) < 16:
        warnings.append("SESSION_SECRET 仍是默认值或过短（少于 16 位），任何人都可能伪造登录状态。"
                        "请在 .env 中改为随机长字符串后重启（改后需要重新登录）。")
    if admin_password_hash:
        import bcrypt
        try:
            if bcrypt.checkpw(b"admin123", admin_password_hash.encode()):
                warnings.append("管理员密码仍是默认的 admin123，请尽快在右上角修改密码。")
        except ValueError:
            pass
    if settings.app_env != "local" and settings.app_debug:
        warnings.append("非本地环境开启了 APP_DEBUG，建议在 .env 中设为 false。")
    return warnings
