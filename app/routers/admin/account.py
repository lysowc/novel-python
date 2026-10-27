"""后台 API：登录 / 登出 / 当前管理员 / 修改密码"""

import bcrypt
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import fail, now, ok
from app.models import Admin
from app.services import security

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============ 登录（无需鉴权） ============

login_router = APIRouter(prefix="/api/admin", tags=["admin-auth"])


@login_router.post("/login")
def login(payload: dict, request: Request, db: Session = Depends(get_db)):
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if not username or not password:
        return fail("请输入账号和密码")
    if security.login_blocked(request, username):
        return fail("登录失败次数过多，请 10 分钟后再试")
    admin = db.query(Admin).filter(Admin.username == username).first()
    if not admin or not _verify_password(password, admin.password):
        security.record_failure(request, username)
        return fail("账号或密码错误")
    security.clear_failures(request, username)
    request.session["admin_id"] = admin.id
    admin.last_login_at = now()
    db.commit()
    return ok({"username": admin.username}, "登录成功")


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


# ============ 鉴权信息 ============

@router.post("/logout")
def logout(request: Request):
    request.session.pop("admin_id", None)
    return ok(None, "已退出登录")


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    admin = db.query(Admin).filter(Admin.id == request.session.get("admin_id")).first()
    return ok({"username": admin.username if admin else ""})


@router.put("/password")
def change_password(payload: dict, request: Request, db: Session = Depends(get_db)):
    old = str(payload.get("old_password") or "")
    new = str(payload.get("new_password") or "")
    if len(new) < 6:
        return fail("新密码至少 6 位")
    admin = db.query(Admin).filter(Admin.id == request.session.get("admin_id")).first()
    if not admin or not _verify_password(old, admin.password):
        return fail("原密码错误")
    admin.password = bcrypt.hashpw(new.encode(), bcrypt.gensalt()).decode()
    db.commit()
    return ok(None, "密码已修改")
