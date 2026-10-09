"""应用配置：.env → pydantic-settings"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_debug: bool = True

    # MySQL（与 PHP 版共用同一库，前端可无缝切换后端）
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_database: str = "ai_novel"
    db_username: str = "root"
    db_password: str = "root"

    # Redis
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_password: str = ""
    redis_database: int = 0

    # 会话
    session_secret: str = "novel-python-secret-change-me"
    session_secure: bool = False
    session_same_site: str = "lax"
    session_cookie: str = "novel_session"
    session_max_age: int = 7 * 24 * 3600

    # AI 调用
    ai_http_timeout: int = 120
    ai_temperature: float = 0.8

    # 前端静态资源目录（默认指向 Vue 构建产物，可改为本项目 static/）
    static_dir: str = "/Users/sora/php/webman/public"

    # 开发跨域（vite dev 5173 直连时用；走代理则不需要）
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"


settings = Settings()
