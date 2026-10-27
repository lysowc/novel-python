"""数据库迁移：python -m commands.migrate [run|fresh]（与 PHP 版 migrate 命令等价）"""
import re
import sys
from pathlib import Path

from sqlalchemy import text

from app.db import engine

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# 1050 表已存在 / 1060 列已存在 / 1061 索引名已存在 / 1022 唯一键重复（同名约束）
ALREADY_APPLIED_CODES = {1050, 1060, 1061, 1022}


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
                    # MySQL 的 DDL 会立即生效、不能回滚：一个迁移执行到一半失败后，前面的语句已经生效。
                    # 重跑时"列/索引/表已存在"说明这一句之前已经执行过，跳过继续，保证迁移可重跑。
                    code = getattr(getattr(e, "orig", None), "args", [None])[0]
                    if code in ALREADY_APPLIED_CODES:
                        print(f"  {name}：已存在，跳过 → {statement.splitlines()[0][:80]}")
                        continue
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
