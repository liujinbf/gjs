import json
import threading
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTreeWidget, QTreeWidgetItem,
    QLabel, QMessageBox, QMenu, QInputDialog, QWidget, QSplitter
)
from app_config import (
    get_runtime_config,
    get_sim_strategy_cooldown_min,
    get_sim_strategy_daily_limit,
    get_sim_strategy_min_rr,
)
from knowledge_governance import apply_strategy_learning_review
from knowledge_scoring import simulate_rule_performance
from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection

class RuleLogicEditorDialog(QDialog):
    saved = Signal()
    load_result_ready = Signal(dict)
    save_result_ready = Signal(dict)
    sandbox_result_ready = Signal(dict)

    def __init__(self, rule_id: int, parent=None):
        super().__init__(parent)
        self.rule_id = rule_id
        self.rule_text = ""
        self.logic_dict = {}
        self.rule_source_type = ""
        self.rule_rationale = ""
        self.last_sandbox_result: dict = {}
        self.load_result_ready.connect(self._on_load_result)
        self.save_result_ready.connect(self._on_save_result)
        self.sandbox_result_ready.connect(self._on_sandbox_result)
        self.setWindowTitle(f"AST 逻辑编译器沙盒 (Rule ID: {rule_id})")
        self.resize(800, 600)
        self._setup_ui()
        self._build_tree(self.logic_dict, self.tree.invisibleRootItem())
        self._set_loading_state(True)
        self._start_load_worker(self._run_load_rule_worker)

    def _load_rule(self) -> dict:
        payload = {
            "ok": True,
            "rule_text": "",
            "logic_dict": {"op": "AND", "conditions": []},
            "source_type": "",
            "rationale": "",
            "error": "",
        }
        try:
            with open_knowledge_connection() as conn:
                row = conn.execute(
                    """
                    SELECT kr.rule_text, kr.logic_json,
                           COALESCE(ks.source_type, '') AS source_type,
                           COALESCE(rg.rationale, '') AS rationale
                    FROM knowledge_rules kr
                    LEFT JOIN knowledge_sources ks ON ks.id = kr.source_id
                    LEFT JOIN rule_governance rg ON rg.rule_id = kr.id AND rg.horizon_min = 30
                    WHERE kr.id = ?
                    LIMIT 1
                    """,
                    (self.rule_id,),
                ).fetchone()
                if row:
                    payload["rule_text"] = str(row["rule_text"] or "")
                    payload["source_type"] = str(row["source_type"] or "")
                    payload["rationale"] = str(row["rationale"] or "")
                    js_str = str(row["logic_json"] or "{}")
                    try:
                        parsed = json.loads(js_str)
                        payload["logic_dict"] = parsed if isinstance(parsed, dict) and parsed else {"op": "AND", "conditions": []}
                    except Exception:
                        payload["logic_dict"] = {"op": "AND", "conditions": []}
            return payload
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "rule_text": "",
                "logic_dict": {"op": "AND", "conditions": []},
                "source_type": "",
                "rationale": "",
                "error": str(exc),
            }

    def _start_load_worker(self, worker) -> None:
        threading.Thread(target=worker, daemon=True, name="rule-logic-load").start()

    def _run_load_rule_worker(self) -> None:
        self.load_result_ready.emit(self._load_rule())

    def _set_loading_state(self, is_loading: bool) -> None:
        if hasattr(self, "tree"):
            self.tree.setEnabled(not is_loading)
        if hasattr(self, "btn_save"):
            self.btn_save.setEnabled(not is_loading)
        if hasattr(self, "btn_simulate"):
            self.btn_simulate.setEnabled(not is_loading)
        if hasattr(self, "info_lbl") and is_loading:
            self.info_lbl.setText("<b>原始规则描述:</b> 正在读取规则，请稍候...")

    def _on_load_result(self, payload: dict) -> None:
        self._set_loading_state(False)
        if not bool((payload or {}).get("ok", False)):
            QMessageBox.critical(self, "错误", f"读取规则失败: {str((payload or {}).get('error', '') or '未知错误')}")
            return
        self.rule_text = str((payload or {}).get("rule_text", "") or "")
        self.rule_source_type = str((payload or {}).get("source_type", "") or "").strip().lower()
        self.rule_rationale = str((payload or {}).get("rationale", "") or "").strip()
        self.logic_dict = dict((payload or {}).get("logic_dict", {}) or {"op": "AND", "conditions": []})
        self.info_lbl.setText(f"<b>原始规则描述:</b> {self.rule_text}")
        self.tree.clear()
        if self._is_strategy_learning_rule():
            self.setWindowTitle(f"策略学习调参沙盒 (Rule ID: {self.rule_id})")
            self.btn_simulate.setText("▶ 策略调参推演")
            self.btn_save.setText("✅ 应用策略调参")
            self.info_lbl.setText(
                f"<b>策略学习建议:</b> {self.rule_text}<br><span style='color:#64748b;'>{self.rule_rationale}</span>"
            )
            self._build_strategy_learning_tree(self.logic_dict)
            self._on_sandbox_result({"ok": True, "result": self._simulate_strategy_learning(self.logic_dict), "error": ""})
        else:
            self.setWindowTitle(f"AST 逻辑编译器沙盒 (Rule ID: {self.rule_id})")
            self.btn_simulate.setText("▶ 一键沙盒回溯引擎")
            self.btn_save.setText("💾 覆盖逻辑并激活规则")
            self._build_tree(self.logic_dict, self.tree.invisibleRootItem())

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self.info_lbl = QLabel(f"<b>原始规则描述:</b> {self.rule_text}")
        self.info_lbl.setWordWrap(True)
        self.info_lbl.setStyleSheet("color: #94a3b8;")
        layout.addWidget(self.info_lbl)

        splitter = QSplitter(Qt.Horizontal)
        
        # Left: Tree
        tree_container = QWidget()
        tree_lay = QVBoxLayout(tree_container)
        tree_lay.setContentsMargins(0,0,0,0)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["逻辑节点", "操作符", "目标参数"])
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 100)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_item_double_click)
        tree_lay.addWidget(self.tree)

        # Right: Sandbox
        sandbox_container = QWidget()
        sandbox_lay = QVBoxLayout(sandbox_container)
        sandbox_lay.setContentsMargins(0,0,0,0)
        
        sandbox_lbl = QLabel("<b>沙盒回测大盘 (近500图谱)</b>")
        self.lbl_sandbox_result = QLabel("等待执行沙盒重演...\n(修改左侧逻辑后点击下方按钮)")
        self.lbl_sandbox_result.setAlignment(Qt.AlignCenter)
        self.lbl_sandbox_result.setStyleSheet("background: #1e293b; color: #cbd5e1; padding: 20px; border-radius: 8px;")
        
        self.btn_simulate = QPushButton("▶ 一键沙盒回溯引擎")
        self.btn_simulate.setStyleSheet("background: #0284c7; color: white; font-weight: bold; padding: 10px;")
        self.btn_simulate.clicked.connect(self._run_sandbox)

        sandbox_lay.addWidget(sandbox_lbl)
        sandbox_lay.addWidget(self.lbl_sandbox_result)
        sandbox_lay.addStretch(1)
        sandbox_lay.addWidget(self.btn_simulate)

        splitter.addWidget(tree_container)
        splitter.addWidget(sandbox_container)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        # Bottom: Actions
        btn_lay = QHBoxLayout()
        self.btn_save = QPushButton("💾 覆盖逻辑并激活规则")
        self.btn_save.setStyleSheet("background: #22c55e; color: white; padding: 8px;")
        self.btn_save.clicked.connect(self._save_and_activate)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_lay.addStretch(1)
        btn_lay.addWidget(btn_cancel)
        btn_lay.addWidget(self.btn_save)
        layout.addLayout(btn_lay)

    def _build_tree(self, node_data: dict, parent_item: QTreeWidgetItem):
        if not node_data:
            node_data = {"op": "AND", "conditions": []}

        if "conditions" in node_data:
            # 这是一个组合节点
            op = node_data.get("op", "AND")
            item = QTreeWidgetItem(parent_item)
            item.setText(0, f"[{op}] 组合")
            item.setData(0, Qt.UserRole, "group")
            item.setData(1, Qt.UserRole, op)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            
            for cond in node_data.get("conditions", []):
                self._build_tree(cond, item)
            item.setExpanded(True)
        else:
            # 这是一个叶子节点
            field = node_data.get("field", "unknown")
            op = node_data.get("op", "==")
            val = node_data.get("value", "")
            
            item = QTreeWidgetItem(parent_item)
            item.setText(0, str(field))
            item.setText(1, str(op))
            item.setText(2, str(val))
            
            item.setData(0, Qt.UserRole, "leaf")
            item.setFlags(item.flags() | Qt.ItemIsEditable)

    def _is_strategy_learning_rule(self) -> bool:
        return self.rule_source_type == "strategy_learning" or str(self.logic_dict.get("source", "")).strip() == "strategy_learning"

    def _build_strategy_learning_tree(self, logic: dict) -> None:
        self.tree.clear()
        root = QTreeWidgetItem(self.tree.invisibleRootItem())
        root.setText(0, "strategy_learning")
        root.setText(1, "专用调参建议")
        root.setText(2, str(logic.get("action_kind", "") or "--"))
        root.setData(0, Qt.UserRole, "strategy_learning")
        root.setFlags(root.flags() & ~Qt.ItemIsEditable)
        fields = [
            ("策略族", "strategy_family"),
            ("策略名称", "strategy_label"),
            ("建议动作", "action_kind"),
            ("统计天数", "days"),
            ("总笔数", "total_count"),
            ("已决笔数", "decided_count"),
            ("胜单", "win_count"),
            ("亏单", "loss_count"),
            ("胜率", "win_rate"),
            ("净盈亏", "net_profit"),
            ("平均RR", "avg_rr"),
        ]
        for label, key in fields:
            child = QTreeWidgetItem(root)
            child.setText(0, label)
            child.setText(1, "=")
            value = logic.get(key, "")
            if key == "win_rate":
                value = f"{float(value or 0.0):.1f}%"
            elif key in {"net_profit", "avg_rr"}:
                value = f"{float(value or 0.0):.2f}"
            child.setText(2, str(value))
            child.setFlags(child.flags() & ~Qt.ItemIsEditable)
        root.setExpanded(True)

    def _simulate_strategy_learning(self, logic: dict) -> dict:
        family = str(logic.get("strategy_family", "") or "").strip().lower()
        action_kind = str(logic.get("action_kind", "") or "").strip().lower()
        config = get_runtime_config()
        current_rr = float(get_sim_strategy_min_rr(family, config=config) or 1.6)
        current_daily_limit = int(get_sim_strategy_daily_limit(family, config=config) or 3)
        current_cooldown_min = int(get_sim_strategy_cooldown_min(family, config=config) or 10)
        loss_count = int(logic.get("loss_count", 0) or 0)
        proposed_rr = current_rr
        proposed_daily_limit = current_daily_limit
        proposed_cooldown_min = current_cooldown_min
        if action_kind == "tighten":
            step = 0.20 if loss_count >= 3 else 0.15
            proposed_rr = min(10.0, round(current_rr + step, 2))
            proposed_daily_limit = max(1, current_daily_limit - 1)
            cooldown_step = 10 if loss_count >= 3 else 5
            proposed_cooldown_min = min(240, current_cooldown_min + cooldown_step)
        return {
            "kind": "strategy_learning",
            "strategy_family": family,
            "strategy_label": str(logic.get("strategy_label", "") or family or "--"),
            "action_kind": action_kind,
            "sandbox_samples": int(logic.get("total_count", 0) or 0),
            "total_matches": int(logic.get("decided_count", 0) or 0),
            "success": int(logic.get("win_count", 0) or 0),
            "mixed": 0,
            "fail": int(logic.get("loss_count", 0) or 0),
            "win_rate": float(logic.get("win_rate", 0.0) or 0.0) / 100.0,
            "score": float(logic.get("net_profit", 0.0) or 0.0),
            "current_rr": current_rr,
            "current_daily_limit": current_daily_limit,
            "current_cooldown_min": current_cooldown_min,
            "proposed_rr": proposed_rr,
            "proposed_daily_limit": proposed_daily_limit,
            "proposed_cooldown_min": proposed_cooldown_min,
        }

    def _tree_to_dict(self, head_item: QTreeWidgetItem) -> dict:
        node_type = head_item.data(0, Qt.UserRole)
        if node_type == "group":
            op = head_item.data(1, Qt.UserRole) or "AND"
            conditions = []
            for i in range(head_item.childCount()):
                conditions.append(self._tree_to_dict(head_item.child(i)))
            return {"op": op, "conditions": conditions}
        else:
            # array support fallback
            val_str = head_item.text(2)
            val = val_str
            if val_str.startswith("[") and val_str.endswith("]"):
                try:
                    val = json.loads(val_str.replace("'", '"'))
                except Exception:
                    val = [x.strip() for x in val_str[1:-1].split(",")]
            elif val_str.isdigit():
                val = int(val_str)
            else:
                try:
                    val = float(val_str)
                except Exception:
                    pass

            return {
                "field": head_item.text(0),
                "op": head_item.text(1),
                "value": val
            }

    def _on_tree_context_menu(self, pos):
        if self._is_strategy_learning_rule():
            return
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if not item:
            act_add_root = menu.addAction("重置为空白 AND 组")
            act_add_root.triggered.connect(lambda: self._build_tree({"op": "AND", "conditions": []}, self.tree.invisibleRootItem()))
        else:
            node_type = item.data(0, Qt.UserRole)
            if node_type == "group":
                act_add_leaf = menu.addAction("+ 新增指标条件 (Leaf)")
                act_add_group = menu.addAction("+ 新增组合嵌套 (Group)")
                menu.addSeparator()
                act_del = menu.addAction("删除此组合")
                
                act_add_leaf.triggered.connect(lambda: self._build_tree({"field": "new_field", "op": "==", "value": ""}, item))
                act_add_group.triggered.connect(lambda: self._build_tree({"op": "AND", "conditions": []}, item))
                act_del.triggered.connect(lambda: item.parent().removeChild(item) if item.parent() else self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item)))
                
            else:
                act_del = menu.addAction("删除此指标条件")
                act_del.triggered.connect(lambda: item.parent().removeChild(item) if item.parent() else self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item)))
                
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def _on_item_double_click(self, item, col):
        if self._is_strategy_learning_rule():
            return
        node_type = item.data(0, Qt.UserRole)
        if node_type == "group" and col == 0:
            current_op = item.data(1, Qt.UserRole) or "AND"
            new_op, ok = QInputDialog.getItem(self, "修改组合", "选择逻辑关系:", ["AND", "OR"], 0 if current_op == "AND" else 1, False)
            if ok:
                item.setText(0, f"[{new_op}] 组合")
                item.setData(1, Qt.UserRole, new_op)
        elif node_type == "leaf":
            self.tree.editItem(item, col)

    def _get_current_logic(self):
        if self._is_strategy_learning_rule():
            return dict(self.logic_dict or {})
        root = self.tree.topLevelItem(0)
        if not root:
            return {}
        return self._tree_to_dict(root)

    def _run_sandbox(self):
        logic = self._get_current_logic()
        if not logic:
            QMessageBox.warning(self, "沙盒限制", "空逻辑无法执行推演。")
            return

        self.btn_simulate.setEnabled(False)
        self.btn_simulate.setText("▶ 策略推演中..." if self._is_strategy_learning_rule() else "▶ 沙盒运转中...")
        self.lbl_sandbox_result.setText("策略调参推演中..." if self._is_strategy_learning_rule() else "沙盒引擎运转中...\n[====================>")
        self._start_sandbox_worker(lambda: self._run_sandbox_worker(logic))

    def _start_sandbox_worker(self, worker) -> None:
        threading.Thread(target=worker, daemon=True, name="rule-logic-sandbox").start()

    def _run_sandbox_worker(self, logic: dict) -> None:
        try:
            if self._is_strategy_learning_rule():
                res = self._simulate_strategy_learning(logic)
            else:
                res = simulate_rule_performance(logic_dict=logic, limit=500)
            self.sandbox_result_ready.emit({"ok": True, "result": dict(res or {}), "error": ""})
        except Exception as exc:  # noqa: BLE001
            self.sandbox_result_ready.emit({"ok": False, "result": {}, "error": str(exc) or "未知错误"})

    def _on_sandbox_result(self, payload: dict) -> None:
        self.btn_simulate.setEnabled(True)
        self.btn_simulate.setText("▶ 策略调参推演" if self._is_strategy_learning_rule() else "▶ 一键沙盒回溯引擎")
        if not bool(payload.get("ok", False)):
            self.lbl_sandbox_result.setText(
                f"<h3 style='color:red;'>沙盒引擎崩溃</h3><p>{str(payload.get('error', '') or '未知错误')}</p>"
            )
            return

        res = dict(payload.get("result", {}) or {})
        self.last_sandbox_result = dict(res)
        try:
            if str(res.get("kind", "") or "") == "strategy_learning":
                wr = float(res.get("win_rate", 0.0) or 0.0) * 100
                score = float(res.get("score", 0.0) or 0.0)
                color = "#ef4444" if score < 0 else "#22c55e"
                action_text = "收紧阈值" if str(res.get("action_kind", "") or "") == "tighten" else "保持观察"
                text = f"""
                <h2 style='color:{color};'>策略学习推演：{action_text}</h2>
                <p>策略族：{str(res.get('strategy_label', '') or '--')}（{str(res.get('strategy_family', '') or '--')}）</p>
                <p>近{int(self.logic_dict.get('days', 7) or 7)}天样本：{int(res.get('sandbox_samples', 0) or 0)} 笔；已决：{int(res.get('total_matches', 0) or 0)} 笔</p>
                <p>胜 / 负 / 胜率 / 净盈亏：{int(res.get('success', 0) or 0)} / {int(res.get('fail', 0) or 0)} / {wr:.1f}% / {score:+.2f}</p>
                <p>RR：{float(res.get('current_rr', 0.0) or 0.0):.2f} → {float(res.get('proposed_rr', 0.0) or 0.0):.2f}</p>
                <p>日上限：{int(res.get('current_daily_limit', 0) or 0)} → {int(res.get('proposed_daily_limit', 0) or 0)}</p>
                <p>冷却：{int(res.get('current_cooldown_min', 0) or 0)} 分钟 → {int(res.get('proposed_cooldown_min', 0) or 0)} 分钟</p>
                """
                self.lbl_sandbox_result.setText(text)
                return
            wr = float(res.get("win_rate", 0.0) or 0.0) * 100
            score = float(res.get("score", 0.0) or 0.0)
            color = "#22c55e" if score > 15 and wr > 50 else ("#f59e0b" if score > 0 else "#ef4444")

            text = f"""
            <h2 style='color:{color};'>假设得分: {score:.1f} / 胜率: {wr:.1f}%</h2>
            <p>沙盒验证样本：{int(res.get('sandbox_samples', 0) or 0)} 口</p>
            <p>特征匹配触发：{int(res.get('total_matches', 0) or 0)} 笔</p>
            <p>其中成功 / 混合 / 失败：{int(res.get('success', 0) or 0)} / {int(res.get('mixed', 0) or 0)} / {int(res.get('fail', 0) or 0)}</p>
            """
            self.lbl_sandbox_result.setText(text)
        except Exception as exc:  # noqa: BLE001
            self.lbl_sandbox_result.setText(f"<h3 style='color:red;'>沙盒引擎崩溃</h3><p>{exc}</p>")

    def _save_and_activate(self):
        logic = self._get_current_logic()
        self.btn_save.setEnabled(False)
        self.btn_save.setText("✅ 正在应用..." if self._is_strategy_learning_rule() else "💾 正在保存...")
        self._start_save_worker(lambda: self._run_save_and_activate(logic))

    def _start_save_worker(self, worker) -> None:
        threading.Thread(target=worker, daemon=True, name="rule-logic-save").start()

    def _run_save_and_activate(self, logic: dict) -> None:
        try:
            if self._is_strategy_learning_rule():
                apply_result = apply_strategy_learning_review(
                    int(self.rule_id),
                    approved=True,
                    db_path=KNOWLEDGE_DB_FILE,
                )
                if not bool(apply_result.get("applied", False)):
                    self.save_result_ready.emit(
                        {
                            "ok": False,
                            "error": str(apply_result.get("reason", "") or "策略学习建议未能应用。"),
                        }
                    )
                    return
                now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                message = str(apply_result.get("message", "") or "").strip()
                with open_knowledge_connection(KNOWLEDGE_DB_FILE) as conn:
                    conn.execute(
                        """
                        INSERT INTO rule_governance (rule_id, horizon_min, governance_status, rationale, updated_at)
                        VALUES (?, 30, 'active', ?, ?)
                        ON CONFLICT(rule_id, horizon_min) DO UPDATE SET
                            governance_status = excluded.governance_status,
                            rationale = excluded.rationale,
                            updated_at = excluded.updated_at
                        """,
                        (
                            int(self.rule_id),
                            f"人工在策略学习沙盒中应用调参；{message}。",
                            now_text,
                        ),
                    )
                    conn.execute(
                        """
                        UPDATE rule_scores
                        SET validation_status = 'validated', updated_at = ?
                        WHERE rule_id = ? AND horizon_min = 30
                        """,
                        (now_text, int(self.rule_id)),
                    )
                self.save_result_ready.emit({"ok": True, "error": "", "message": message})
                return
            sandbox = simulate_rule_performance(logic_dict=logic, limit=500)
            total_matches = int(sandbox.get("total_matches", 0) or 0)
            win_rate = float(sandbox.get("win_rate", 0.0) or 0.0)
            score = float(sandbox.get("score", 0.0) or 0.0)
            if total_matches < 10:
                self.save_result_ready.emit(
                    {
                        "ok": False,
                        "error": f"沙盒匹配样本不足（仅 {total_matches} 笔），禁止激活。请收窄/修正逻辑后重跑。",
                    }
                )
                return
            if win_rate < 0.50 or score <= 0:
                self.save_result_ready.emit(
                    {
                        "ok": False,
                        "error": f"沙盒表现不达标（胜率 {win_rate * 100:.1f}%，得分 {score:.1f}），禁止激活。",
                    }
                )
                return
            self.last_sandbox_result = dict(sandbox)
            now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open_knowledge_connection() as conn:
                conn.execute(
                    "UPDATE knowledge_rules SET logic_json = ? WHERE id = ?",
                    (json.dumps(logic, ensure_ascii=False), self.rule_id)
                )
                conn.execute(
                    """
                    INSERT INTO rule_scores (rule_id, horizon_min, validation_status, updated_at)
                    VALUES (?, 30, 'validated', ?)
                    ON CONFLICT(rule_id, horizon_min) DO UPDATE SET
                        validation_status = excluded.validation_status,
                        updated_at = excluded.updated_at
                    """,
                    (self.rule_id, now_text),
                )
                conn.execute(
                    """
                    INSERT INTO rule_governance (rule_id, horizon_min, governance_status, rationale, updated_at)
                    VALUES (?, 30, 'active', ?, ?)
                    ON CONFLICT(rule_id, horizon_min) DO UPDATE SET
                        governance_status = excluded.governance_status,
                        rationale = excluded.rationale,
                        updated_at = excluded.updated_at
                    """,
                    (
                        self.rule_id,
                        (
                            "人工完成结构化逻辑编译，且沙盒推演达标："
                            f"匹配 {total_matches} 笔，胜率 {win_rate * 100:.1f}%，得分 {score:.1f}。"
                        ),
                        now_text,
                    ),
                )
            self.save_result_ready.emit({"ok": True, "error": ""})
        except Exception as exc:  # noqa: BLE001
            self.save_result_ready.emit({"ok": False, "error": str(exc) or "未知错误"})

    def _on_save_result(self, payload: dict) -> None:
        self.btn_save.setEnabled(True)
        self.btn_save.setText("✅ 应用策略调参" if self._is_strategy_learning_rule() else "💾 覆盖逻辑并激活规则")
        if bool(payload.get("ok", False)):
            if self._is_strategy_learning_rule():
                message = str(payload.get("message", "") or "").strip()
                QMessageBox.information(self, "调参成功", message or "策略学习建议已应用到模拟盘策略参数。")
            else:
                QMessageBox.information(self, "部署成功", "沙盒逻辑已即时覆写进主节点引擎，正式拥有开仓提权！")
            self.saved.emit()
            self.accept()
            return
        QMessageBox.critical(self, "错误", f"保存失败: {str(payload.get('error', '') or '未知错误')}")
