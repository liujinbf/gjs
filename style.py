# style.py
"""
UI Styles definitions to centralize styling and remove hardcoded CSS from ui.py.
"""

# ================================
# Typography
# ================================
STYLE_TITLE_PRIMARY = "font-size:28px;font-weight:800;color:#0f172a;"
STYLE_SUBTITLE = "color:#64748b;font-size:13px;"

STYLE_SECTION_TITLE = "font-size:16px;font-weight:800;color:#0f172a;"
STYLE_CARD_TITLE = "font-size:15px;font-weight:800;color:#0f172a;"
STYLE_SMALL_TITLE = "font-size:14px;font-weight:800;color:#0f172a;"

# ================================
# Containers & Cards
# ================================
STYLE_CARD_CONTAINER = "QFrame{background:#ffffff;border:1px solid #e2e8f0;border-radius:16px;}"
STYLE_METRIC_CARD = "background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;color:#0f172a;font-size:14px;font-weight:800;padding:8px;"

# ================================
# Info Panels (Labels with text)
# ================================
STYLE_PANEL_NEUTRAL = "background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:10px;color:#334155;font-size:12px;line-height:1.5;"
STYLE_PANEL_ACCENT = "background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;padding:10px;color:#1d4ed8;font-size:12px;line-height:1.5;"
STYLE_PANEL_SUCCESS = "background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;padding:10px;color:#166534;font-size:12px;line-height:1.5;"
STYLE_PANEL_WARNING_LIGHT = "background:#fffaf0;border:1px solid #fde68a;border-radius:12px;padding:10px;color:#7c2d12;font-size:12px;line-height:1.5;"
STYLE_PANEL_WARNING = "background:#fff7ed;border:1px solid #fdba74;border-radius:12px;padding:10px;color:#9a3412;font-size:12px;line-height:1.5;"

# Bold versions for Trade Grade
STYLE_PANEL_NEUTRAL_BOLD = "background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:10px;color:#334155;font-size:12px;line-height:1.6;font-weight:700;"
STYLE_PANEL_ACCENT_BOLD = "background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;padding:10px;color:#1d4ed8;font-size:12px;line-height:1.6;font-weight:700;"
STYLE_PANEL_SUCCESS_BOLD = "background:#ecfdf5;border:1px solid #bbf7d0;border-radius:12px;padding:10px;color:#166534;font-size:12px;line-height:1.6;font-weight:700;"
STYLE_PANEL_WARNING_BOLD = "background:#fff7ed;border:1px solid #fdba74;border-radius:12px;padding:10px;color:#9a3412;font-size:12px;line-height:1.6;font-weight:700;"

# ================================
# Text Editors (Logs, Briefs)
# ================================
STYLE_TEXT_NEUTRAL = "background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;color:#334155;font-size:12px;padding:4px;"
STYLE_TEXT_ACCENT = "background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;color:#1e3a8a;font-size:12px;padding:4px;"
STYLE_TEXT_WARNING = "background:#fffaf0;border:1px solid #fde68a;border-radius:12px;color:#7c2d12;font-size:12px;padding:4px;"
STYLE_TEXT_LOG = "background:#0f172a;border:none;border-radius:12px;color:#e2e8f0;font-size:12px;padding:6px;font-family:Consolas, monospace;"

# ================================
# TabWidget Base Style
# ================================
STYLE_TAB_WIDGET = """
QTabWidget::pane {
    border: 1px solid #e2e8f0;
    background: #ffffff;
    border-radius: 12px;
}
QTabBar::tab {
    background: #f1f5f9;
    color: #475569;
    border: 1px solid #e2e8f0;
    border-bottom-color: #e2e8f0; /* same as pane color */
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    min-width: 80px;
    padding: 8px 12px;
    font-size: 13px;
    font-weight: bold;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #0f172a;
    border-bottom-color: #ffffff; /* hide line below selected tab */
}
QTabBar::tab:hover:!selected {
    background: #e2e8f0;
}
"""

# ================================
# Status Badges
# ================================
STYLE_BADGE_NEUTRAL = "background:#e2e8f0;color:#334155;border-radius:12px;padding:6px 14px;font-weight:800;"
STYLE_BADGE_SUCCESS = "background:#dcfce7;color:#166534;border-radius:12px;padding:6px 14px;font-weight:800;"
STYLE_BADGE_NEGATIVE = "background:#fee2e2;color:#b91c1c;border-radius:12px;padding:6px 14px;font-weight:800;"
STYLE_BADGE_ACCENT = "background:#dbeafe;color:#1d4ed8;border-radius:12px;padding:6px 14px;font-weight:800;"
STYLE_BADGE_WARNING = "background:#fef3c7;color:#92400e;border-radius:12px;padding:6px 14px;font-weight:800;"

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
    "success": "#ecfdf5",
    "warning": "#fff7ed",
    "accent": "#eff6ff",
    "neutral": "#f8fafc",
}
