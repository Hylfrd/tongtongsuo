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
        connection.execute("DELETE FROM cabinets WHERE code='A'")
        connection.execute("UPDATE cabinets SET code='Renamed',name='New name' WHERE code='B'")
    Database(path)
    with database.transaction() as connection:
        assert [tuple(row) for row in connection.execute("SELECT code,name FROM cabinets")] == [("Renamed", "New name")]
