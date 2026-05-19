"""
运行库维护入口：统计并压缩可安全减重的历史 payload。

默认只做 dry-run 估算；加 --apply 后才写入 SQLite。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from knowledge_base import KNOWLEDGE_DB_FILE
from knowledge_governance import compact_learning_reports, summarize_learning_report_storage
from ui import (
    BACKGROUND_OUTBOX_DB,
    compact_done_background_outbox_payloads,
    summarize_background_outbox_storage,
)


DEFAULT_BACKUP_DIR = Path(__file__).resolve().parent / ".runtime" / "backups"
BACKUP_FILE_PATTERNS = (
    "knowledge_base_*.db",
    "background_outbox_*.sqlite",
)


def _sqlite_file_stats(path: Path) -> dict:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "size_bytes": 0,
            "size_mb": 0.0,
            "page_count": 0,
            "freelist_count": 0,
            "reclaimable_mb": 0.0,
        }
    page_count = 0
    page_size = 4096
    freelist_count = 0
    journal_mode = ""
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10) as conn:
            page_count = int(conn.execute("PRAGMA page_count").fetchone()[0] or 0)
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0] or 4096)
            freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0] or 0)
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0] or "")
    except sqlite3.Error as exc:
        return {
            "path": str(path),
            "exists": True,
            "size_bytes": path.stat().st_size,
            "size_mb": round(path.stat().st_size / 1024 / 1024, 3),
            "error": str(exc),
        }
    reclaimable_bytes = freelist_count * page_size
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "size_mb": round(path.stat().st_size / 1024 / 1024, 3),
        "page_count": page_count,
        "page_size": page_size,
        "freelist_count": freelist_count,
        "journal_mode": journal_mode,
        "reclaimable_bytes": reclaimable_bytes,
        "reclaimable_mb": round(reclaimable_bytes / 1024 / 1024, 3),
    }


def _check_write_lock_available(path: Path, timeout: float = 1.0) -> dict:
    if not path.exists():
        return {"ok": False, "reason": "数据库文件不存在。"}
    try:
        with sqlite3.connect(str(path), timeout=timeout) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
    except sqlite3.Error as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "reason": ""}


def _backup_sqlite_database(source: Path, backup_dir: Path | None = None) -> dict:
    target_dir = backup_dir or DEFAULT_BACKUP_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = target_dir / f"{source.stem}_{stamp}{source.suffix}"
    with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30) as src:
        with sqlite3.connect(str(backup_path), timeout=30) as dst:
            src.backup(dst)
    return {
        "path": str(backup_path),
        "size_bytes": backup_path.stat().st_size if backup_path.exists() else 0,
        "size_mb": round((backup_path.stat().st_size if backup_path.exists() else 0) / 1024 / 1024, 3),
    }


def vacuum_sqlite_database(
    path: Path | str,
    apply: bool = False,
    backup_dir: Path | str | None = None,
    min_reclaimable_mb: float = 64.0,
) -> dict:
    """带备份的 SQLite 物理回收；默认 dry-run，不写库。"""
    target = Path(path)
    before = _sqlite_file_stats(target)
    if not before.get("exists"):
        return {
            "applied": False,
            "skipped": True,
            "reason": "数据库文件不存在。",
            "before": before,
            "after": before,
        }
    reclaimable_mb = float(before.get("reclaimable_mb", 0.0) or 0.0)
    if reclaimable_mb < float(min_reclaimable_mb or 0.0):
        return {
            "applied": False,
            "skipped": True,
            "reason": f"可回收空间约 {reclaimable_mb:.3f}MB，低于阈值 {float(min_reclaimable_mb or 0.0):.3f}MB。",
            "before": before,
            "after": before,
        }
    lock_check = _check_write_lock_available(target)
    if not lock_check.get("ok"):
        return {
            "applied": False,
            "skipped": True,
            "reason": f"数据库当前不可安全写入：{lock_check.get('reason', '')}",
            "before": before,
            "after": before,
        }
    if not apply:
        return {
            "applied": False,
            "skipped": False,
            "dry_run": True,
            "reason": "dry-run：加 --apply 后才会备份并执行 VACUUM。",
            "before": before,
            "after": before,
        }

    backup = _backup_sqlite_database(target, Path(backup_dir) if backup_dir else DEFAULT_BACKUP_DIR)
    with sqlite3.connect(str(target), timeout=60, isolation_level=None) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA optimize")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    after = _sqlite_file_stats(target)
    return {
        "applied": True,
        "skipped": False,
        "dry_run": False,
        "backup": backup,
        "before": before,
        "after": after,
        "recovered_size_bytes": max(0, int(before.get("size_bytes", 0) or 0) - int(after.get("size_bytes", 0) or 0)),
        "recovered_size_mb": round(
            max(0, int(before.get("size_bytes", 0) or 0) - int(after.get("size_bytes", 0) or 0)) / 1024 / 1024,
            3,
        ),
    }


def collect_runtime_storage_health(
    knowledge_db_path: Path | str | None = None,
    outbox_db_path: Path | str | None = None,
) -> dict:
    knowledge_path = Path(knowledge_db_path) if knowledge_db_path else KNOWLEDGE_DB_FILE
    outbox_path = Path(outbox_db_path) if outbox_db_path else BACKGROUND_OUTBOX_DB
    return {
        "knowledge_db": _sqlite_file_stats(knowledge_path),
        "background_outbox_db": _sqlite_file_stats(outbox_path),
        "learning_reports": summarize_learning_report_storage(db_path=knowledge_path),
        "background_outbox": summarize_background_outbox_storage(db_path=outbox_path),
    }


def vacuum_runtime_databases(
    apply: bool = False,
    target: str = "all",
    knowledge_db_path: Path | str | None = None,
    outbox_db_path: Path | str | None = None,
    backup_dir: Path | str | None = None,
    min_reclaimable_mb: float = 64.0,
) -> dict:
    knowledge_path = Path(knowledge_db_path) if knowledge_db_path else KNOWLEDGE_DB_FILE
    outbox_path = Path(outbox_db_path) if outbox_db_path else BACKGROUND_OUTBOX_DB
    clean_target = str(target or "all").strip().lower()
    selected: list[tuple[str, Path]] = []
    if clean_target in {"all", "knowledge"}:
        selected.append(("knowledge_db", knowledge_path))
    if clean_target in {"all", "outbox", "background_outbox"}:
        selected.append(("background_outbox_db", outbox_path))
    if not selected:
        return {
            "applied": False,
            "target": clean_target,
            "error": "target 只能是 all、knowledge、outbox。",
            "results": {},
        }
    results = {
        name: vacuum_sqlite_database(
            path,
            apply=apply,
            backup_dir=Path(backup_dir) if backup_dir else DEFAULT_BACKUP_DIR,
            min_reclaimable_mb=min_reclaimable_mb,
        )
        for name, path in selected
    }
    return {
        "applied": bool(apply),
        "target": clean_target,
        "backup_dir": str(Path(backup_dir) if backup_dir else DEFAULT_BACKUP_DIR),
        "results": results,
    }


def _iter_runtime_backup_files(backup_dir: Path | str | None = None) -> list[Path]:
    target_dir = Path(backup_dir) if backup_dir else DEFAULT_BACKUP_DIR
    if not target_dir.exists():
        return []
    files: list[Path] = []
    for pattern in BACKUP_FILE_PATTERNS:
        files.extend(path for path in target_dir.glob(pattern) if path.is_file())
    return sorted(set(files), key=lambda path: (path.stat().st_mtime, path.name), reverse=True)


def summarize_runtime_backups(backup_dir: Path | str | None = None) -> dict:
    target_dir = Path(backup_dir) if backup_dir else DEFAULT_BACKUP_DIR
    files = _iter_runtime_backup_files(target_dir)
    total_bytes = sum(path.stat().st_size for path in files)
    return {
        "backup_dir": str(target_dir),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / 1024 / 1024, 3),
        "files": [
            {
                "path": str(path),
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "size_mb": round(path.stat().st_size / 1024 / 1024, 3),
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
            for path in files
        ],
    }


def prune_runtime_backups(
    apply: bool = False,
    backup_dir: Path | str | None = None,
    keep_latest: int = 0,
) -> dict:
    """清理运行库维护备份；默认 dry-run，只删除明确匹配的维护备份文件。"""
    target_dir = (Path(backup_dir) if backup_dir else DEFAULT_BACKUP_DIR).resolve()
    project_dir = Path(__file__).resolve().parent
    try:
        target_dir.relative_to(project_dir)
    except ValueError:
        return {
            "applied": False,
            "error": "为避免误删，备份目录必须位于项目目录内。",
            "backup_dir": str(target_dir),
            "deleted": [],
        }
    files = _iter_runtime_backup_files(target_dir)
    before_total_bytes = sum(path.stat().st_size for path in files)
    keep = max(0, int(keep_latest or 0))
    kept_files = files[:keep]
    delete_files = files[keep:]
    kept = [
        {
            "path": str(path),
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "size_mb": round(path.stat().st_size / 1024 / 1024, 3),
        }
        for path in kept_files
    ]
    deleted = []
    for path in delete_files:
        item = {
            "path": str(path),
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "size_mb": round(path.stat().st_size / 1024 / 1024, 3),
        }
        deleted.append(item)
        if apply:
            path.unlink()
    reclaimed_bytes = sum(int(item["size_bytes"]) for item in deleted)
    return {
        "applied": bool(apply),
        "backup_dir": str(target_dir),
        "keep_latest": keep,
        "before": {
            "file_count": len(files),
            "total_bytes": before_total_bytes,
            "total_mb": round(before_total_bytes / 1024 / 1024, 3),
        },
        "kept": kept,
        "deleted": deleted,
        "reclaimed_bytes": reclaimed_bytes if apply else 0,
        "estimated_reclaimable_bytes": reclaimed_bytes,
        "estimated_reclaimable_mb": round(reclaimed_bytes / 1024 / 1024, 3),
    }


def compact_runtime_storage(
    apply: bool = False,
    knowledge_db_path: Path | str | None = None,
    outbox_db_path: Path | str | None = None,
    keep_full_reports: int = 72,
    batch_size: int = 500,
    max_batches: int = 1,
) -> dict:
    knowledge_path = Path(knowledge_db_path) if knowledge_db_path else KNOWLEDGE_DB_FILE
    outbox_path = Path(outbox_db_path) if outbox_db_path else BACKGROUND_OUTBOX_DB
    dry_run = not bool(apply)
    before = collect_runtime_storage_health(knowledge_path, outbox_path)
    learning_result = compact_learning_reports(
        db_path=knowledge_path,
        keep_full_count=keep_full_reports,
        batch_size=batch_size,
        max_batches=max_batches,
        dry_run=dry_run,
    )
    outbox_result = compact_done_background_outbox_payloads(
        db_path=outbox_path,
        batch_size=batch_size * max(1, int(max_batches or 1)),
        dry_run=dry_run,
    )
    after = collect_runtime_storage_health(knowledge_path, outbox_path)
    return {
        "applied": bool(apply),
        "before": before,
        "learning_reports": learning_result,
        "background_outbox": outbox_result,
        "after": after,
        "note": "SQLite 文件物理体积需要 VACUUM 后才会明显回收；本命令只压缩 payload 内容。",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="贵金属机器人运行库健康检查与 payload 压缩")
    parser.add_argument("--apply", action="store_true", help="实际写入压缩结果；默认只 dry-run")
    parser.add_argument("--health-only", action="store_true", help="只输出健康统计，不估算压缩")
    parser.add_argument("--vacuum", action="store_true", help="执行带备份的 SQLite VACUUM；默认 dry-run")
    parser.add_argument("--backup-health", action="store_true", help="只输出运行库维护备份体积")
    parser.add_argument("--prune-backups", action="store_true", help="清理运行库维护备份；默认 dry-run")
    parser.add_argument("--vacuum-target", choices=["all", "knowledge", "outbox"], default="all", help="VACUUM 目标库")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR, help="VACUUM 前 SQLite 备份目录")
    parser.add_argument("--keep-latest-backups", type=int, default=0, help="清理备份时保留最近多少个备份文件")
    parser.add_argument("--min-reclaimable-mb", type=float, default=64.0, help="低于该可回收空间时跳过 VACUUM")
    parser.add_argument("--keep-full-reports", type=int, default=72, help="保留最近多少条完整学习报告")
    parser.add_argument("--batch-size", type=int, default=500, help="每批最多处理多少条记录")
    parser.add_argument("--max-batches", type=int, default=1, help="最多处理多少批")
    parser.add_argument("--knowledge-db", type=Path, default=KNOWLEDGE_DB_FILE, help="知识库 SQLite 路径")
    parser.add_argument("--outbox-db", type=Path, default=BACKGROUND_OUTBOX_DB, help="后台 outbox SQLite 路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.health_only:
        result = collect_runtime_storage_health(args.knowledge_db, args.outbox_db)
    elif args.backup_health:
        result = summarize_runtime_backups(args.backup_dir)
    elif args.prune_backups:
        result = prune_runtime_backups(
            apply=bool(args.apply),
            backup_dir=args.backup_dir,
            keep_latest=args.keep_latest_backups,
        )
    elif args.vacuum:
        result = vacuum_runtime_databases(
            apply=bool(args.apply),
            target=args.vacuum_target,
            knowledge_db_path=args.knowledge_db,
            outbox_db_path=args.outbox_db,
            backup_dir=args.backup_dir,
            min_reclaimable_mb=args.min_reclaimable_mb,
        )
    else:
        result = compact_runtime_storage(
            apply=bool(args.apply),
            knowledge_db_path=args.knowledge_db,
            outbox_db_path=args.outbox_db,
            keep_full_reports=args.keep_full_reports,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
