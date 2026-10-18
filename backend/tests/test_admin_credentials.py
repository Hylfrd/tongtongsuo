from app.admin_credentials import AdminCredentials
from app.admin_password import create_password_hash, parse_password_hash, verify_password


def test_persisted_hash_is_bootstrapped_once_and_replace_is_atomic(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    old = create_password_hash("old-admin-pass")
    store = AdminCredentials(old, path)
    encoded, revision = store.snapshot()
    assert encoded == old
    new = create_password_hash("admin")
    assert store.replace(revision, new)
    assert not store.replace(revision, old)
    restarted = AdminCredentials(old, path)
    assert restarted.snapshot() == (new, revision + 1)
    assert verify_password("admin", parse_password_hash(restarted.snapshot()[0]))
    assert not verify_password("old-admin-pass", parse_password_hash(restarted.snapshot()[0]))
    assert AdminCredentials("invalid-bootstrap", path).snapshot() == restarted.snapshot()


def test_invalid_bootstrap_and_memory_instances_do_not_share_state():
    first = AdminCredentials("not-a-hash")
    assert first.snapshot() == ("", 0)
    assert first.replace(0, create_password_hash("admin"))
    assert AdminCredentials("").snapshot() == ("", 0)
