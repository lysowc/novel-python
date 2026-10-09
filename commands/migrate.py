"""数据库迁移：python -m commands.migrate [run|fresh]（与 PHP 版 migrate 命令等价）"""
import re
import sys
from pathlib import Path

from sqlalchemy import text

from app.db import engine

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def ensure_migrations_table(conn) -> None:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS `migrations` ("
        "`name` VARCHAR(255) NOT NULL, `applied_at` DATETIME NULL, PRIMARY KEY (`name`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    ))


def run_migrations(action: str) -> int:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print("没有找到迁移文件（migrations/*.sql）")
        return 0

    with engine.begin() as conn:
        ensure_migrations_table(conn)
        if action == "fresh":
            print("清空全部表...")
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            rows = conn.execute(text("SHOW TABLES")).fetchall()
            for row in rows:
                conn.execute(text(f"DROP TABLE IF EXISTS `{row[0]}`"))
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
            ensure_migrations_table(conn)
            print("已清空")

        applied = {row[0] for row in conn.execute(text("SELECT `name` FROM `migrations`"))}

        for file in files:
            name = file.name
            if name in applied:
                print(f"跳过 {name}（已执行）")
                continue
            sql = file.read_text(encoding="utf-8")
            statements = re.split(r";\s*\n", sql)
            for statement in statements:
                lines = [l for l in statement.splitlines()
                         if l.strip() and not l.strip().startswith("--")]
                statement = "\n".join(lines).strip()
                if not statement:
                    continue
                try:
                    conn.execute(text(statement))
                except Exception as e:
                    print(f"{name} 执行失败: {e}")
                    print("SQL:", statement[:200])
                    return 1
            conn.execute(text("INSERT INTO `migrations` (`name`, `applied_at`) VALUES (:n, NOW())"),
                         {"n": name})
            print(f"已执行 {name}")
    print("迁移完成 ✔")
    return 0


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "run"
    sys.exit(run_migrations(action))
