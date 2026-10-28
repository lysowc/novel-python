"""后台 API（/api/admin/*，除 login 外均需登录）——与 PHP 版契约逐一对应

按领域拆分为子模块，这里汇总成一个 router（main.py 统一加登录校验）。
"""
from fastapi import APIRouter

from app.routers.admin import account, ai_config, dashboard, ideas, novels, oplogs, settings
from app.routers.admin.formatters import (  # noqa: F401  兼容旧的 from app.routers.admin import f_xxx
    f_category, f_novel, f_chapter, f_chapter_full, f_idea, f_provider, f_model, f_task, f_log,
)
from app.routers.admin.novels import _recount_novel  # noqa: F401

login_router = account.login_router

router = APIRouter()
for _module in (account, dashboard, novels, ideas, ai_config, settings, oplogs):
    router.include_router(_module.router)
