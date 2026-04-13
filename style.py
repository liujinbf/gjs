# style.py
"""
UI Styles definitions to centralize styling and remove hardcoded CSS from ui.py.
Using a modern, premium, "glass/flat" mixed aesthetic with soft colors and rounded corners.
"""

# ================================
# Typography
# ================================
STYLE_TITLE_PRIMARY = "font-size:24px; font-weight:900; color:#1e293b; font-family:'Segoe UI', 'Microsoft YaHei', sans-serif; letter-spacing:1px;"
STYLE_SUBTITLE = "color:#64748b; font-size:13px; font-family:'Segoe UI', 'Microsoft YaHei', sans-serif;"

STYLE_SECTION_TITLE = "font-size:16px; font-weight:800; color:#334155; padding-bottom:4px; border-bottom:2px solid #e2e8f0; margin-bottom:8px;"
STYLE_CARD_TITLE = "font-size:14px; font-weight:800; color:#475569;"
STYLE_SMALL_TITLE = "font-size:13px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.5px;"

# ================================
# Containers & Cards
# ================================
# Removing harsh borders, using subtle background colors and rounded corners to simulate soft depth.
STYLE_CARD_CONTAINER = """
QFrame {
    background: white;
    border: 1px solid #f1f5f9;
    border-bottom: 2px solid #e2e8f0;
    border-radius: 14px;
}
"""

STYLE_METRIC_CARD = """
background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffffff, stop:1 #f8fafc);
border: 1px solid #f1f5f9;
border-bottom: 2px solid #e2e8f0;
border-radius: 12px;
color: #0f172a;
font-size: 15px;
font-weight: 800;
padding: 12px;
"""

# ================================
# Info Panels (Labels with text)
# ================================
# We make them softer, remove harsh borders, give them a colored background to differentiate tone
_PANEL_BASE = "border-radius:10px; padding:8px 12px; font-size:13px; line-height:1.6;"

STYLE_PANEL_NEUTRAL = f"background:#f8fafc; color:#475569; border:1px solid #f1f5f9; {_PANEL_BASE}"
STYLE_PANEL_ACCENT = f"background:#eff6ff; color:#1d4ed8; border:1px solid #dbeafe; {_PANEL_BASE}"
STYLE_PANEL_SUCCESS = f"background:#f0fdf4; color:#15803d; border:1px solid #dcfce7; {_PANEL_BASE}"
STYLE_PANEL_WARNING_LIGHT = f"background:#fffbeb; color:#92400e; border:1px solid #fef3c7; {_PANEL_BASE}"
STYLE_PANEL_WARNING = f"background:#fff7ed; color:#c2410c; border:1px solid #ffedd5; {_PANEL_BASE}"

# Bold versions for Trade Grade
STYLE_PANEL_NEUTRAL_BOLD = f"{STYLE_PANEL_NEUTRAL} font-weight:700;"
STYLE_PANEL_ACCENT_BOLD = f"{STYLE_PANEL_ACCENT} font-weight:700;"
STYLE_PANEL_SUCCESS_BOLD = f"{STYLE_PANEL_SUCCESS} font-weight:700;"
STYLE_PANEL_WARNING_BOLD = f"{STYLE_PANEL_WARNING} font-weight:700;"

# ================================
# Text Editors (Logs, Briefs)
# ================================
STYLE_TEXT_NEUTRAL = "background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; color:#475569; font-size:13px; padding:8px;"
STYLE_TEXT_ACCENT = "background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px; color:#1e40af; font-size:13px; padding:8px; font-weight:500;"
STYLE_TEXT_WARNING = "background:#fffbeb; border:1px solid #fde68a; border-radius:10px; color:#92400e; font-size:13px; padding:8px;"
STYLE_TEXT_LOG = "background:#0f172a; border:2px solid #1e293b; border-radius:10px; color:#38bdf8; font-size:12px; padding:10px; font-family:'Cascadia Code', Consolas, monospace;"

# ================================
# TabWidget Base Style
# ================================
# Modern underline-based tabs
STYLE_TAB_WIDGET = """
QTabWidget::pane {
    border: none;
    border-top: 1px solid #e2e8f0;
    background: transparent;
}
QTabBar::tab {
    background: transparent;
    color: #64748b;
    border: none;
    border-bottom: 3px solid transparent;
    padding: 10px 20px;
    font-size: 14px;
    font-weight: 800;
    margin-right: 4px;
}
QTabBar::tab:selected {
    color: #0ea5e9;
    border-bottom: 3px solid #0ea5e9;
}
QTabBar::tab:hover:!selected {
    color: #334155;
    border-bottom: 3px solid #cbd5e1;
}
"""

# ================================
# Status Badges
# ================================
# Slightly softer pill badges
_BADGE_BASE = "border-radius:14px; padding:6px 16px; font-weight:800; font-size:12px;"

STYLE_BADGE_NEUTRAL = f"background:#f1f5f9; color:#475569; border:1px solid #e2e8f0; {_BADGE_BASE}"
STYLE_BADGE_SUCCESS = f"background:#dcfce7; color:#166534; border:1px solid #bbf7d0; {_BADGE_BASE}"
STYLE_BADGE_NEGATIVE = f"background:#fee2e2; color:#b91c1c; border:1px solid #fecaca; {_BADGE_BASE}"
STYLE_BADGE_ACCENT = f"background:#dbeafe; color:#1d4ed8; border:1px solid #bfdbfe; {_BADGE_BASE}"
STYLE_BADGE_WARNING = f"background:#fef3c7; color:#92400e; border:1px solid #fde68a; {_BADGE_BASE}"

BADGE_STYLE_MAP = {
    "success": STYLE_BADGE_SUCCESS,
    "negative": STYLE_BADGE_NEGATIVE,
    "accent": STYLE_BADGE_ACCENT,
    "warning": STYLE_BADGE_WARNING,
    "neutral": STYLE_BADGE_NEUTRAL,
}

PANEL_STYLE_MAP = {
    "success": STYLE_PANEL_SUCCESS,
    "warning": STYLE_PANEL_WARNING,
    "accent": STYLE_PANEL_ACCENT,
    "neutral": STYLE_PANEL_NEUTRAL,
}

GRADE_STYLE_MAP = {
    "success": STYLE_PANEL_SUCCESS_BOLD,
    "warning": STYLE_PANEL_WARNING_BOLD,
    "accent": STYLE_PANEL_ACCENT_BOLD,
    "neutral": STYLE_PANEL_NEUTRAL_BOLD,
}

TABLE_ROW_BG_MAP = {
    "success": "#f0fdf4",
    "warning": "#fff7ed",
    "accent": "#eff6ff",
    "neutral": "#ffffff",
}

# ================================
# Global Application Stylesheet
# ================================
GLOBAL_APP_STYLE = """
/* Make the entire app background softer */
QMainWindow, QDialog {
    background-color: #f8fafc;
}

/* Beautiful modern scrollbars */
QScrollBar:vertical {
    border: none;
    background: #f1f5f9;
    width: 10px;
    margin: 0px 0px 0px 0px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #cbd5e1;
    min-height: 30px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #94a3b8;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    border: none;
    background: none;
}

QScrollBar:horizontal {
    border: none;
    background: #f1f5f9;
    height: 10px;
    margin: 0px 0px 0px 0px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: #cbd5e1;
    min-width: 30px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover {
    background: #94a3b8;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    border: none;
    background: none;
}

/* Premium Table look */
QTableWidget {
    background-color: white;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    gridline-color: transparent;
    selection-background-color: transparent;
    font-size: 13px;
    color: #334155;
    outline: none;
}
QTableWidget::item {
    border-bottom: 1px solid #f1f5f9;
    padding: 8px;
}
QHeaderView::section {
    background-color: #f8fafc;
    color: #64748b;
    font-weight: bold;
    font-size: 13px;
    border: none;
    border-bottom: 2px solid #e2e8f0;
    padding: 10px;
}
QTableCornerButton::section {
    background-color: #f8fafc;
    border: none;
}

/* Modern inputs */
QLineEdit, QSpinBox, QComboBox {
    background: white;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 6px 12px;
    color: #1e293b;
    font-size: 13px;
    min-height: 24px;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 2px solid #3b82f6;
    background: #ffffff;
}

/* Base PushButtons */
QPushButton {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-bottom: 2px solid #cbd5e1;
    border-radius: 8px;
    color: #334155;
    font-weight: 700;
    padding: 6px 16px;
    font-size: 13px;
}
QPushButton:hover {
    background: #f8fafc;
    border-color: #94a3b8;
    border-bottom-color: #94a3b8;
    color: #0f172a;
}
QPushButton:pressed {
    background: #e2e8f0;
    border-bottom: 1px solid #cbd5e1;
    margin-top: 1px;
}

/* Primary/Action buttons */
QPushButton[type="primary"] {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3b82f6, stop:1 #2563eb);
    border: 1px solid #1d4ed8;
    border-bottom: 2px solid #1d4ed8;
    color: white;
}
QPushButton[type="primary"]:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #60a5fa, stop:1 #3b82f6);
}
QPushButton[type="primary"]:pressed {
    background: #1d4ed8;
    border-bottom: 1px solid #1e40af;
}
"""
