"""
知识库底座：负责初始化 SQLite、导入 Markdown 资料、抽取候选规则与登记来源。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from app_config import PROJECT_DIR

RUNTIME_DIR = PROJECT_DIR / ".runtime"
KNOWLEDGE_DB_FILE = RUNTIME_DIR / "knowledge_base.db"
SQLITE_TIMEOUT_SEC = 15.0

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*\S)\s*$")
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*\S)\s*$")

CATEGORY_KEYWORDS = {
    "entry": ("入场", "买入", "建仓", "开仓", "抄底"),
    "exit": ("出场", "止盈", "止损", "离场", "平仓"),
    "trend": ("趋势", "反转", "突破", "回踩", "支撑", "压力", "关键位"),
    "risk": ("风险", "仓位", "杠杆", "爆仓", "保证金", "回撤"),
    "psychology": ("心态", "纪律", "情绪", "贪婪", "恐惧", "复盘"),
    "allocation": ("资金", "配置", "分仓", "比例"),
    "directional": ("做多", "做空", "多空", "顺势", "逆势"),
    "case": ("案例", "教训", "实战", "失败", "成功"),
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _normalize_tags(tags: list[str] | tuple[str, ...] | None) -> list[str]:
    seen = set()
    result = []
    for item in list(tags or []):
        text = _normalize_text(item).lower().replace(" ", "_")
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _infer_category(*parts: str) -> str:
    text = " ".join(_normalize_text(item) for item in parts).lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword.lower() in text for keyword in keywords):
            return category
    return "general"


def _infer_asset_scope(*parts: str) -> str:
    text = " ".join(_normalize_text(item) for item in parts).upper()
    has_gold = any(keyword in text for keyword in ("黄金", "XAU", "XAUUSD", "GLD", "沪金"))
    has_silver = any(keyword in text for keyword in ("白银", "XAG", "XAGUSD", "沪银"))
    if has_gold and has_silver:
        return "XAUUSD,XAGUSD"
    if has_gold:
        return "XAUUSD"
    if has_silver:
        return "XAGUSD"
    return "ALL"


def _looks_like_table_separator(line: str) -> bool:
    text = line.replace("|", "").replace("-", "").replace(":", "").strip()
    return not text


def _table_cells_to_rule(line: str) -> str:
    cells = [_normalize_text(item) for item in line.strip().strip("|").split("|")]
    cells = [item for item in cells if item]
    if len(cells) < 2:
        return ""
    header_tokens = {"判断维度", "关键指标", "参考标准", "方法", "具体操作", "维度", "说明", "项目", "类型", "周期", "对比项"}
    if any(cell in header_tokens for cell in cells):
        return ""
    if len(cells) >= 3:
        return _normalize_text(f"{cells[-2]}：{cells[-1]}")
    return _normalize_text("：".join(cells))


def _iter_candidate_rules(markdown_text: str) -> list[dict]:
    current_heading = ""
    current_category = "general"
    result = []
    for raw_line in str(markdown_text or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            current_heading = _normalize_text(heading_match.group(2))
            current_category = _infer_category(current_heading)
            continue

        stripped = line.strip()
        if stripped.startswith(">"):
            continue

        candidate_text = ""
        if stripped.startswith(("-", "*")):
            candidate_text = _normalize_text(stripped[1:])
        else:
            numbered_match = _NUMBERED_RE.match(stripped)
            if numbered_match:
                candidate_text = _normalize_text(numbered_match.group(1))
            elif "|" in stripped and not _looks_like_table_separator(stripped):
                candidate_text = _table_cells_to_rule(stripped)

        if not candidate_text:
            continue
        if len(candidate_text) < 6:
            continue

        category = _infer_category(current_heading, candidate_text)
        if category == "general":
            category = current_category

        tags = _normalize_tags([current_heading, category])
        result.append(
            {
                "section_title": current_heading,
                "category": category or "general",
                "asset_scope": _infer_asset_scope(current_heading, candidate_text),
                "rule_text": candidate_text,
                "confidence": "working",
                "evidence_type": "经验整理",
                "tags": tags,
            }
        )
    return result


def _open_sqlite(target: Path) -> sqlite3.Connection:
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=SQLITE_TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def open_knowledge_connection(
    db_path: Path | str | None = None,
    ensure_schema: bool = True,
) -> sqlite3.Connection:
    target = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    if ensure_schema:
        init_knowledge_base(db_path=target)
    return _open_sqlite(target)


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    return _open_sqlite(target)


def init_knowledge_base(db_path: Path | str | None = None) -> Path:
    target = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    with _connect(target) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL,
                location TEXT NOT NULL,
                author TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                trust_level TEXT NOT NULL DEFAULT 'working',
                tags_json TEXT NOT NULL DEFAULT '[]',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_type, location)
            );

            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                content TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES knowledge_sources(id),
                UNIQUE(source_id, content_hash)
            );

            CREATE TABLE IF NOT EXISTS knowledge_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                document_id INTEGER,
                section_title TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'general',
                asset_scope TEXT NOT NULL DEFAULT 'ALL',
                rule_text TEXT NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'working',
                evidence_type TEXT NOT NULL DEFAULT '经验整理',
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES knowledge_sources(id),
                FOREIGN KEY (document_id) REFERENCES knowledge_documents(id),
                UNIQUE(source_id, category, rule_text)
            );

            CREATE INDEX IF NOT EXISTS idx_knowledge_rules_category ON knowledge_rules(category);
            CREATE INDEX IF NOT EXISTS idx_knowledge_rules_asset_scope ON knowledge_rules(asset_scope);
            CREATE INDEX IF NOT EXISTS idx_knowledge_sources_trust_level ON knowledge_sources(trust_level);

            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                latest_price REAL NOT NULL DEFAULT 0,
                spread_points REAL NOT NULL DEFAULT 0,
                has_live_quote INTEGER NOT NULL DEFAULT 0,
                tone TEXT NOT NULL DEFAULT 'neutral',
                trade_grade TEXT NOT NULL DEFAULT '',
                trade_grade_source TEXT NOT NULL DEFAULT '',
                alert_state_text TEXT NOT NULL DEFAULT '',
                event_risk_mode_text TEXT NOT NULL DEFAULT '',
                event_active_name TEXT NOT NULL DEFAULT '',
                event_importance_text TEXT NOT NULL DEFAULT '',
                event_note TEXT NOT NULL DEFAULT '',
                signal_side TEXT NOT NULL DEFAULT 'neutral',
                regime_tag TEXT NOT NULL DEFAULT '',
                regime_text TEXT NOT NULL DEFAULT '',
                feature_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(snapshot_time, symbol)
            );

            CREATE TABLE IF NOT EXISTS snapshot_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                snapshot_time TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,
                future_snapshot_time TEXT NOT NULL DEFAULT '',
                future_price REAL NOT NULL DEFAULT 0,
                future_spread_points REAL NOT NULL DEFAULT 0,
                price_change_pct REAL NOT NULL DEFAULT 0,
                max_price REAL NOT NULL DEFAULT 0,
                min_price REAL NOT NULL DEFAULT 0,
                mfe_pct REAL NOT NULL DEFAULT 0,
                mae_pct REAL NOT NULL DEFAULT 0,
                outcome_label TEXT NOT NULL DEFAULT 'unknown',
                signal_quality TEXT NOT NULL DEFAULT 'neutral',
                labeled_at TEXT NOT NULL,
                FOREIGN KEY (snapshot_id) REFERENCES market_snapshots(id),
                UNIQUE(snapshot_id, horizon_min)
            );

            CREATE INDEX IF NOT EXISTS idx_market_snapshots_symbol_time ON market_snapshots(symbol, snapshot_time);
            CREATE INDEX IF NOT EXISTS idx_snapshot_outcomes_symbol_horizon ON snapshot_outcomes(symbol, horizon_min);

            CREATE TABLE IF NOT EXISTS rule_snapshot_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                snapshot_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                matched_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (rule_id) REFERENCES knowledge_rules(id),
                FOREIGN KEY (snapshot_id) REFERENCES market_snapshots(id),
                UNIQUE(rule_id, snapshot_id)
            );

            CREATE TABLE IF NOT EXISTS rule_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                horizon_min INTEGER NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                mixed_count INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0,
                observe_count INTEGER NOT NULL DEFAULT 0,
                success_rate REAL NOT NULL DEFAULT 0,
                score REAL NOT NULL DEFAULT 0,
                validation_status TEXT NOT NULL DEFAULT 'insufficient',
                last_processed_outcome_id INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (rule_id) REFERENCES knowledge_rules(id),
                UNIQUE(rule_id, horizon_min)
            );

            CREATE INDEX IF NOT EXISTS idx_rule_snapshot_matches_rule ON rule_snapshot_matches(rule_id);
            CREATE INDEX IF NOT EXISTS idx_rule_snapshot_matches_snapshot ON rule_snapshot_matches(snapshot_id);
            CREATE INDEX IF NOT EXISTS idx_rule_scores_status ON rule_scores(validation_status, horizon_min);

            CREATE TABLE IF NOT EXISTS rule_governance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                horizon_min INTEGER NOT NULL,
                governance_status TEXT NOT NULL DEFAULT 'pending',
                rationale TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                FOREIGN KEY (rule_id) REFERENCES knowledge_rules(id),
                UNIQUE(rule_id, horizon_min)
            );

            CREATE TABLE IF NOT EXISTS learning_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_type TEXT NOT NULL DEFAULT 'rule_digest',
                horizon_min INTEGER NOT NULL DEFAULT 30,
                summary_text TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER,
                symbol TEXT NOT NULL DEFAULT '',
                snapshot_time TEXT NOT NULL DEFAULT '',
                feedback_label TEXT NOT NULL DEFAULT '',
                feedback_score REAL NOT NULL DEFAULT 0,
                feedback_text TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'manual',
                signature TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (snapshot_id) REFERENCES market_snapshots(id),
                UNIQUE(signature)
            );

            CREATE TABLE IF NOT EXISTS rule_feedback_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                helpful_count INTEGER NOT NULL DEFAULT 0,
                unhelpful_count INTEGER NOT NULL DEFAULT 0,
                too_late_count INTEGER NOT NULL DEFAULT 0,
                noise_count INTEGER NOT NULL DEFAULT 0,
                risky_count INTEGER NOT NULL DEFAULT 0,
                total_count INTEGER NOT NULL DEFAULT 0,
                positive_rate REAL NOT NULL DEFAULT 0,
                negative_rate REAL NOT NULL DEFAULT 0,
                score REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (rule_id) REFERENCES knowledge_rules(id),
                UNIQUE(rule_id)
            );

            CREATE INDEX IF NOT EXISTS idx_rule_governance_status ON rule_governance(governance_status, horizon_min);
            CREATE INDEX IF NOT EXISTS idx_learning_reports_type_time ON learning_reports(report_type, created_at);
            CREATE INDEX IF NOT EXISTS idx_user_feedback_snapshot_time ON user_feedback(symbol, snapshot_time, created_at);
            CREATE INDEX IF NOT EXISTS idx_user_feedback_label ON user_feedback(feedback_label, created_at);
            CREATE INDEX IF NOT EXISTS idx_rule_feedback_scores_score ON rule_feedback_scores(score, total_count);

            CREATE TABLE IF NOT EXISTS ai_signal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_signature TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                snapshot_time TEXT NOT NULL DEFAULT '',
                snapshot_symbols_json TEXT NOT NULL DEFAULT '[]',
                symbol TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT 'neutral',
                entry_price REAL NOT NULL DEFAULT 0,
                stop_loss REAL NOT NULL DEFAULT 0,
                take_profit REAL NOT NULL DEFAULT 0,
                signal_schema_version TEXT NOT NULL DEFAULT '',
                signal_meta_valid INTEGER NOT NULL DEFAULT 0,
                signal_meta_reason TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                api_base TEXT NOT NULL DEFAULT '',
                is_fallback INTEGER NOT NULL DEFAULT 0,
                push_sent INTEGER NOT NULL DEFAULT 0,
                summary_line TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                signal_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(signal_signature)
            );

            CREATE INDEX IF NOT EXISTS idx_ai_signal_events_time ON ai_signal_events(occurred_at, symbol);
            CREATE INDEX IF NOT EXISTS idx_ai_signal_events_action ON ai_signal_events(action, signal_meta_valid);

            -- 3.2 修复：系统状态 KV 表，代替直接读写 JSON 文件，避免写入中断导致文件损坏
            CREATE TABLE IF NOT EXISTS system_state_kv (
                key   TEXT PRIMARY KEY,
                value_json TEXT NOT NULL DEFAULT 'null',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            """
        )
        # 3.3 修复：为已有数据库热迁移 last_processed_outcome_id 列（SQLite 不支持 IF NOT EXISTS 修饰 ALTER COLUMN）
        try:
            conn.execute(
                "ALTER TABLE rule_scores ADD COLUMN last_processed_outcome_id INTEGER NOT NULL DEFAULT 0"
            )
        except Exception:
            pass  # 列已存在，忽略
        try:
            conn.execute(
                "ALTER TABLE market_snapshots ADD COLUMN regime_tag TEXT NOT NULL DEFAULT ''"
            )
        except Exception:
            pass
        try:
            conn.execute(
                "ALTER TABLE market_snapshots ADD COLUMN regime_text TEXT NOT NULL DEFAULT ''"
            )
        except Exception:
            pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_market_snapshots_regime_time ON market_snapshots(regime_tag, snapshot_time)"
        )
    return target



def upsert_source(
    title: str,
    source_type: str,
    location: str,
    author: str = "",
    published_at: str = "",
    trust_level: str = "working",
    tags: list[str] | tuple[str, ...] | None = None,
    notes: str = "",
    db_path: Path | str | None = None,
) -> int:
    init_knowledge_base(db_path=db_path)
    tags_json = json.dumps(_normalize_tags(tags), ensure_ascii=False)
    now_text = _now_text()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO knowledge_sources (
                title, source_type, location, author, published_at, trust_level, tags_json, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_type, location) DO UPDATE SET
                title=excluded.title,
                author=excluded.author,
                published_at=excluded.published_at,
                trust_level=excluded.trust_level,
                tags_json=excluded.tags_json,
                notes=excluded.notes,
                updated_at=excluded.updated_at
            """,
            (
                _normalize_text(title) or "未命名来源",
                _normalize_text(source_type) or "unknown",
                _normalize_text(location),
                _normalize_text(author),
                _normalize_text(published_at),
                _normalize_text(trust_level) or "working",
                tags_json,
                _normalize_text(notes),
                now_text,
                now_text,
            ),
        )
        row = conn.execute(
            "SELECT id FROM knowledge_sources WHERE source_type = ? AND location = ?",
            (_normalize_text(source_type) or "unknown", _normalize_text(location)),
        ).fetchone()
        return int(row["id"])


def extract_candidate_rules(markdown_text: str) -> list[dict]:
    return _iter_candidate_rules(markdown_text)


def import_markdown_source(
    file_path: Path | str,
    title: str | None = None,
    author: str = "用户资料",
    trust_level: str = "user_note",
    tags: list[str] | tuple[str, ...] | None = None,
    notes: str = "",
    db_path: Path | str | None = None,
) -> dict:
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")
    source_id = upsert_source(
        title=title or path.stem,
        source_type="local_markdown",
        location=str(path),
        author=author,
        published_at="",
        trust_level=trust_level,
        tags=tags or ["local", "markdown"],
        notes=notes,
        db_path=db_path,
    )

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    imported_at = _now_text()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO knowledge_documents (source_id, content_hash, content, imported_at)
            VALUES (?, ?, ?, ?)
            """,
            (source_id, content_hash, content, imported_at),
        )
        row = conn.execute(
            "SELECT id FROM knowledge_documents WHERE source_id = ? AND content_hash = ?",
            (source_id, content_hash),
        ).fetchone()
        document_id = int(row["id"])

        rules = extract_candidate_rules(content)
        inserted_rules = 0
        for item in rules:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO knowledge_rules (
                    source_id, document_id, section_title, category, asset_scope, rule_text, confidence, evidence_type, tags_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    document_id,
                    _normalize_text(item.get("section_title", "")),
                    _normalize_text(item.get("category", "")) or "general",
                    _normalize_text(item.get("asset_scope", "")) or "ALL",
                    _normalize_text(item.get("rule_text", "")),
                    _normalize_text(item.get("confidence", "")) or "working",
                    _normalize_text(item.get("evidence_type", "")) or "经验整理",
                    json.dumps(_normalize_tags(item.get("tags", [])), ensure_ascii=False),
                    imported_at,
                ),
            )
            if cursor.rowcount > 0:
                inserted_rules += 1

    return {
        "source_id": source_id,
        "document_id": document_id,
        "rule_count": inserted_rules,
        "content_hash": content_hash,
    }


def seed_external_sources(sources: list[dict], db_path: Path | str | None = None) -> int:
    count = 0
    for item in list(sources or []):
        upsert_source(
            title=str(item.get("title", "") or "").strip(),
            source_type=str(item.get("source_type", "") or "web_reference").strip(),
            location=str(item.get("location", "") or "").strip(),
            author=str(item.get("author", "") or "").strip(),
            published_at=str(item.get("published_at", "") or "").strip(),
            trust_level=str(item.get("trust_level", "") or "working").strip(),
            tags=list(item.get("tags", []) or []),
            notes=str(item.get("notes", "") or "").strip(),
            db_path=db_path,
        )
        count += 1
    return count


def summarize_knowledge_base(db_path: Path | str | None = None) -> dict:
    init_knowledge_base(db_path=db_path)
    with _connect(db_path) as conn:
        source_count = int(conn.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0])
        document_count = int(conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0])
        rule_count = int(conn.execute("SELECT COUNT(*) FROM knowledge_rules").fetchone()[0])
        local_source_count = int(
            conn.execute("SELECT COUNT(*) FROM knowledge_sources WHERE source_type = 'local_markdown'").fetchone()[0]
        )
        external_source_count = source_count - local_source_count
    return {
        "source_count": source_count,
        "document_count": document_count,
        "rule_count": rule_count,
        "local_source_count": local_source_count,
        "external_source_count": external_source_count,
        "summary_text": (
            f"当前知识库共登记 {source_count} 个来源，其中本地资料 {local_source_count} 个，"
            f"外部来源 {external_source_count} 个；已入库文档 {document_count} 份，候选规则 {rule_count} 条。"
        ),
    }


# ── 3.2 修复：系统状态 KV 表读写接口 ──────────────────────────────────────

def kv_get(key: str, default=None, db_path: Path | str | None = None):
    """从 system_state_kv 读取一个键的值（已 JSON 解码）。不存在时返回 default。"""
    init_knowledge_base(db_path=db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT value_json FROM system_state_kv WHERE key = ?", (str(key),)
        ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return default


def kv_set(key: str, value, db_path: Path | str | None = None) -> None:
    """向 system_state_kv 写入一个键值对（value 会被 JSON 编码）。"""
    init_knowledge_base(db_path=db_path)
    value_json = json.dumps(value, ensure_ascii=False)
    now_text = _now_text()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO system_state_kv (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (str(key), value_json, now_text),
        )
