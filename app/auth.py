"""后台鉴权：签名的会话 Cookie（与 PHP session 等价，前端零改动）"""
from fastapi import Depends, Request

from app.helpers import fail_401


def get_admin_id(request: Request) -> int:
    return int(request.session.get("admin_id") or 0)


def require_admin(request: Request) -> int:
    admin_id = get_admin_id(request)
    if not admin_id:
        raise AuthError()
    return admin_id


class AuthError(Exception):
    """未登录异常 → 401"""


def auth_dependency(request: Request) -> int:
    admin_id = get_admin_id(request)
    if not admin_id:
        raise AuthError()
    return admin_id


async def auth_exception_handler(request: Request, exc: AuthError):
    return fail_401()


def current_admin(request: Request) -> int:
    return require_admin(request)
