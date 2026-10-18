"""Manually add the requested B-cabinet demonstration inventory once."""

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


MARKER = "manual_b_cabinet_preview_v1"
DEMO_NOTE = "演示数据，非真实库存。"
TOOLS = (
    ("剪刀", "Scissors", 2, "把", "pc", "toolbox"),
    ("美工刀", "Utility knife", 2, "把", "pc", "toolbox"),
    ("活动扳手", "Adjustable wrench", 2, "把", "pc", "pliers"),
    ("内六角扳手套装", "Hex key set", 1, "套", "set", "toolbox"),
    ("水平尺", "Spirit level", 1, "把", "pc", "tape"),
    ("手电筒", "Flashlight", 3, "支", "pc", "toolbox"),
    ("热熔胶枪", "Hot glue gun", 1, "把", "pc", "toolbox"),
    ("绝缘胶带", "Electrical tape", 3, "卷", "roll", "tape"),
)


def seed_preview(database_path: Path):
    # Open an existing database only: Database() would run startup migrations
    # and could recreate a missing cabinet before this script checks for it.
    connection = sqlite3.connect(database_path.resolve().as_uri() + "?mode=rw", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (MARKER,)).fetchone():
            connection.rollback()
            return {"changed": False, "message": "演示数据已添加过；未修改现有数据。"}
        cabinet = connection.execute("SELECT id,code,name FROM cabinets WHERE code='B'").fetchone()
        if cabinet is None:
            raise ValueError("未找到 B 区柜子，未添加任何数据。")
        if connection.execute("SELECT 1 FROM tools WHERE cabinet_id=? AND name='打气筒'", (cabinet["id"],)).fetchone():
            raise ValueError("B 区已有打气筒，不能覆盖现有库存或借用记录；未添加任何数据。")
        pump = connection.execute(
            """INSERT INTO tools (cabinet_id,name,name_en,quantity,unit,unit_en,icon,notes)
            VALUES (?,'打气筒','Air pump',1,'个','pc','toolbox',?)""",
            (cabinet["id"], DEMO_NOTE),
        ).lastrowid
        borrowed_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        connection.execute(
            """INSERT INTO loans (user_name,school_code,cabinet_id,cabinet_name,cabinet_code,
                tool_id,tool_name,quantity,borrowed_at,returned_at)
            VALUES ('测试借用人（演示）','DEMO-001',?,?,?,?,'打气筒',1,?,NULL)""",
            (cabinet["id"], cabinet["name"], cabinet["code"], pump, borrowed_at),
        )
        added_tools = 1
        for name, name_en, quantity, unit, unit_en, icon in TOOLS:
            cursor = connection.execute(
                """INSERT INTO tools (cabinet_id,name,name_en,quantity,unit,unit_en,icon,notes)
                SELECT ?,?,?,?,?,?,?,?
                WHERE NOT EXISTS (SELECT 1 FROM tools WHERE cabinet_id=? AND name=?)""",
                (cabinet["id"], name, name_en, quantity, unit, unit_en, icon, DEMO_NOTE, cabinet["id"], name),
            )
            added_tools += cursor.rowcount
        connection.execute("INSERT INTO schema_migrations (name) VALUES (?)", (MARKER,))
        connection.commit()
        return {"changed": True, "cabinet_code": "B", "added_tools": added_tools, "added_demo_loans": 1}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="手动添加一次 B 区演示工具和非真实借用记录。")
    parser.add_argument("--database", required=True, type=Path, help="已有应用 SQLite 数据库路径")
    args = parser.parse_args()
    try:
        result = seed_preview(args.database)
    except (sqlite3.Error, ValueError) as error:
        parser.exit(1, f"添加失败：{error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
