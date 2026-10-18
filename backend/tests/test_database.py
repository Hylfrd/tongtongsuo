import sqlite3

from app.database import Database, SCHEMA


def test_legacy_database_upgrade_preserves_inventory_and_custom_cabinet(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO cabinets (id,code,name,group_name,notes) VALUES (9,'A','Custom A','Existing','Keep me')")
        connection.execute("INSERT INTO cabinets (id,code,name) VALUES (10,'C','Custom C')")
        connection.execute("INSERT INTO tools (id,cabinet_id,name,quantity,notes) VALUES (7,9,'Legacy tool',12,'Inventory notes')")
    database = Database(path)
    Database(path)
    with database.transaction() as connection:
        cabinets = {row["code"]: dict(row) for row in connection.execute("SELECT * FROM cabinets")}
        assert set(cabinets) == {"A", "B", "C"}
        assert cabinets["A"]["id"] == 9
        assert cabinets["A"]["name"] == "Custom A"
        assert cabinets["A"]["group_name"] == "Existing"
        assert cabinets["A"]["notes"] == "Keep me"
        assert cabinets["B"]["name"] == cabinets["B"]["group_name"] == "B区"
        tool = dict(connection.execute("SELECT * FROM tools WHERE id=7").fetchone())
        assert (tool["cabinet_id"], tool["quantity"], tool["notes"]) == (9, 12, "Inventory notes")
        assert (tool["unit"], tool["unit_en"], tool["name_en"], tool["description_en"]) == ("件", "", "", "")


def test_deleted_initial_cabinet_does_not_reappear(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    database = Database(path)
    with database.transaction(write=True) as connection:
        connection.execute("DELETE FROM tools")
        connection.execute("DELETE FROM cabinets WHERE code='A'")
        connection.execute("UPDATE cabinets SET code='Renamed',name='New name' WHERE code='B'")
    Database(path)
    with database.transaction() as connection:
        assert [tuple(row) for row in connection.execute("SELECT code,name FROM cabinets")] == [("Renamed", "New name")]


def test_initial_inventory_is_editable_once_without_device_fixtures(tmp_path):
    path = tmp_path / "seeded.sqlite3"
    database = Database(path)
    with database.transaction(write=True) as connection:
        for code in ("A", "B"):
            rows = connection.execute("SELECT t.* FROM tools t JOIN cabinets c ON c.id=t.cabinet_id WHERE c.code=? ORDER BY t.id", (code,)).fetchall()
            assert [row["name_en"] for row in rows] == ["Screwdriver", "Tape measure", "Claw hammer", "Needle-nose pliers"]
            assert all((row["quantity"], row["unit"], row["unit_en"]) == (1, "把", "pc") for row in rows)
        assert connection.execute("SELECT COUNT(*) FROM locks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM gateways").fetchone()[0] == 0
        connection.execute("UPDATE tools SET quantity=7 WHERE id=1")
        connection.execute("DELETE FROM tools WHERE id=2")
        connection.execute("UPDATE tools SET archived=1 WHERE id=3")
    Database(path)
    Database(path)
    with database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM tools").fetchone()[0] == 7
        assert connection.execute("SELECT quantity FROM tools WHERE id=1").fetchone()[0] == 7
        assert connection.execute("SELECT 1 FROM tools WHERE id=2").fetchone() is None
        assert connection.execute("SELECT archived FROM tools WHERE id=3").fetchone()[0] == 1


def test_inventory_seed_respects_existing_named_and_archived_tools(tmp_path):
    path = tmp_path / "legacy-seeded.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO cabinets (id,code,name) VALUES (1,'A','Existing')")
        connection.execute("INSERT INTO tools (id,cabinet_id,name,quantity) VALUES (1,1,'螺丝刀',9)")
        connection.execute("INSERT INTO tools (id,cabinet_id,name,quantity,archived) VALUES (2,1,'卷尺',3,1)")
    database = Database(path)
    Database(path)
    with database.transaction() as connection:
        rows = connection.execute("SELECT name,quantity,archived FROM tools WHERE cabinet_id=1 ORDER BY id").fetchall()
        assert len(rows) == 4
        assert tuple(rows[0]) == ("螺丝刀", 9, 0)
        assert tuple(rows[1]) == ("卷尺", 3, 1)
