"""初始化系统：python -m commands.install [-u 账号] [-p 密码]

等价于 PHP 版 app:install：迁移 + 管理员/分类/Prompt/系统配置，
旧版默认 Prompt 按指纹自动升级，用户自定义内容保留。
"""
import argparse
import sys

import bcrypt
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.helpers import now
from app.models import Admin, Category, Prompt, SystemConfig
from app.services.prompt_defaults import CONFIG_DEFAULTS, DEFAULT_CATEGORIES, PROMPT_DEFAULTS
from commands.migrate import run_migrations


def seed_prompts(db: Session) -> None:
    for item in PROMPT_DEFAULTS:
        row = db.query(Prompt).filter(Prompt.type == item["type"]).first()
        if not row:
            db.add(Prompt(
                type=item["type"], name=item["name"], description=item["description"],
                content=item["content"].strip(), updated_at=now(),
            ))
            continue
        # 指纹升级：仍是旧版默认内容才替换
        is_stock_old = False
        if item["type"] == "memory_update":
            is_stock_old = '"main_character"' in (row.content or "") and '"schema"' not in (row.content or "")
        elif item["type"] in ("chapter_generate", "chapter_continue"):
            is_stock_old = "历史章节摘要" in (row.content or "") and "检索召回" not in (row.content or "")
        if is_stock_old:
            row.name = item["name"]
            row.description = item["description"]
            row.content = item["content"].strip()
            row.updated_at = now()
            print(f"  已升级 Prompt：{item['type']}（旧版默认内容 → 新版）")
    db.commit()


def seed_config(db: Session) -> None:
    existing = {c.key for c in db.query(SystemConfig).all()}
    for key, value, desc in CONFIG_DEFAULTS:
        row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if key not in existing:
            db.add(SystemConfig(key=key, value=value, description=desc, updated_at=now()))
        elif row and not row.description:
            row.description = desc
    db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化系统")
    parser.add_argument("-u", "--username", default="admin", help="管理员账号")
    parser.add_argument("-p", "--password", default="admin123", help="管理员密码")
    args = parser.parse_args()

    print("[1/4] 执行数据库迁移...")
    if run_migrations("run") != 0:
        return 1

    db = SessionLocal()
    try:
        print("[2/4] 写入管理员...")
        admin = db.query(Admin).filter(Admin.username == args.username).first()
        if not admin:
            admin = Admin(username=args.username)
            db.add(admin)
        admin.password = bcrypt.hashpw(args.password.encode(), bcrypt.gensalt()).decode()
        admin.updated_at = now()
        db.commit()
        print(f"  管理员账号: {args.username}  密码: {args.password}")

        print("[3/4] 写入默认分类...")
        if db.query(Category).count() == 0:
            for i, name in enumerate(DEFAULT_CATEGORIES):
                db.add(Category(name=name, description="", sort=i + 1, status=1,
                                created_at=now(), updated_at=now()))
            db.commit()

        print("[4/4] 写入默认 Prompt 与系统配置...")
        seed_prompts(db)
        seed_config(db)
    finally:
        db.close()

    print("初始化完成 ✔  启动服务: uvicorn app.main:app --port 8800")
    return 0


if __name__ == "__main__":
    sys.exit(main())
