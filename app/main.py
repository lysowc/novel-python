"""FastAPI 应用入口：中间件 / 鉴权 / 路由 / SPA 静态兜底"""
import os

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import AuthError, require_admin
from app.config import settings
from app.routers import admin, ai_tasks, front

app = FastAPI(title="AI 小说工坊", docs_url=None, redoc_url=None)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie=settings.session_cookie,
    same_site=settings.session_same_site,
    https_only=settings.session_secure,
    max_age=settings.session_max_age,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.exception_handler(AuthError)
async def auth_error_handler(request: Request, exc: AuthError):
    return JSONResponse(status_code=401, content={"code": 401, "msg": "未登录或登录已过期", "data": None})


# 前台（无需鉴权）
app.include_router(front.router)
# 后台登录（无需鉴权）
app.include_router(admin.login_router)
# 后台（需登录）
app.include_router(admin.router, dependencies=[Depends(require_admin)])
app.include_router(ai_tasks.router, dependencies=[Depends(require_admin)])

# ============ SPA 静态资源与兜底 ============

_static_dir = os.path.abspath(settings.static_dir)
if os.path.isdir(_static_dir):
    assets_dir = os.path.join(_static_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    for favicon in ("favicon.ico", "favicon.png", "favicon.svg"):
        fp = os.path.join(_static_dir, favicon)
        if os.path.isfile(fp):

            @app.get("/" + favicon, include_in_schema=False)
            def _favicon(fp=fp):
                return FileResponse(fp)

            break


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str, request: Request):
    if full_path == "api" or full_path.startswith("api/"):
        return JSONResponse(status_code=200, content={"code": 404, "msg": "接口不存在", "data": None})
    index_file = os.path.join(_static_dir, "index.html")
    if os.path.isfile(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>前端尚未构建</h1><p>请运行 pnpm build（web 目录）</p>", status_code=200)
