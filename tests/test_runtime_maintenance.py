import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime_maintenance import prune_runtime_backups, summarize_runtime_backups, vacuum_runtime_databases, vacuum_sqlite_database


def _build_fragmented_db(path: Path) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO items (payload) VALUES (?)",
            [("x" * 8192,) for _ in range(160)],
        )
        conn.execute("DELETE FROM items WHERE id <= 120")
        conn.commit()


def test_vacuum_sqlite_database_dry_run_keeps_file_untouched(tmp_path):
    db_path = tmp_path / "sample.sqlite"
    backup_dir = tmp_path / "backups"
    _build_fragmented_db(db_path)
    before_size = db_path.stat().st_size

    result = vacuum_sqlite_database(
        db_path,
        apply=False,
        backup_dir=backup_dir,
        min_reclaimable_mb=0.0,
    )

    assert result["dry_run"] is True
    assert result["applied"] is False
    assert db_path.stat().st_size == before_size
    assert not backup_dir.exists()


def test_vacuum_sqlite_database_apply_creates_backup_and_preserves_rows(tmp_path):
    db_path = tmp_path / "sample.sqlite"
    backup_dir = tmp_path / "backups"
    _build_fragmented_db(db_path)

    result = vacuum_sqlite_database(
        db_path,
        apply=True,
        backup_dir=backup_dir,
        min_reclaimable_mb=0.0,
    )

    assert result["applied"] is True
    backup_path = Path(result["backup"]["path"])
    assert backup_path.exists()
    with sqlite3.connect(str(db_path)) as conn:
        live_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    with sqlite3.connect(str(backup_path)) as conn:
        backup_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert live_count == 40
    assert backup_count == 40


def test_vacuum_runtime_databases_rejects_unknown_target(tmp_path):
    result = vacuum_runtime_databases(
        apply=False,
        target="unknown",
        knowledge_db_path=tmp_path / "knowledge.db",
        outbox_db_path=tmp_path / "outbox.sqlite",
    )

    assert result["applied"] is False
    assert "target" in result["error"]


def test_prune_runtime_backups_dry_run_keeps_files(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old_backup = backup_dir / "knowledge_base_20260518_120000.db"
    new_backup = backup_dir / "background_outbox_20260518_130000.sqlite"
    ignored = backup_dir / "manual-note.txt"
    old_backup.write_bytes(b"a" * 10)
    new_backup.write_bytes(b"b" * 20)
    ignored.write_text("keep", encoding="utf-8")

    result = prune_runtime_backups(apply=False, backup_dir=backup_dir, keep_latest=1)
    summary = summarize_runtime_backups(backup_dir)

    assert result["applied"] is False
    assert len(result["deleted"]) == 1
    assert old_backup.exists()
    assert new_backup.exists()
    assert ignored.exists()
    assert summary["file_count"] == 2


def test_prune_runtime_backups_apply_removes_only_matching_backups(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup_one = backup_dir / "knowledge_base_20260518_120000.db"
    backup_two = backup_dir / "background_outbox_20260518_130000.sqlite"
    ignored = backup_dir / "manual-note.txt"
    backup_one.write_bytes(b"a" * 10)
    backup_two.write_bytes(b"b" * 20)
    ignored.write_text("keep", encoding="utf-8")

    result = prune_runtime_backups(apply=True, backup_dir=backup_dir, keep_latest=0)

    assert result["applied"] is True
    assert result["reclaimed_bytes"] == 30
    assert not backup_one.exists()
    assert not backup_two.exists()
    assert ignored.exists()
