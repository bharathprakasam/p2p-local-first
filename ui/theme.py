"""ui/theme.py - PhantomLink v2 theme. All colors defined here."""

BG_VOID     = "#060810"
BG_BASE     = "#0b0f1a"
BG_SURFACE  = "#0f1525"
BG_CARD     = "#141e30"
BG_ELEVATED = "#1a2540"

CYAN        = "#00ffe7"
CYAN_DIM    = "#00b8a5"
CYAN_GLOW   = "rgba(0,255,231,0.12)"
CYAN_GLOW2  = "rgba(0,255,231,0.06)"
MAGENTA     = "#e040fb"
MAGENTA_DIM = "#9c27b0"
MAGENTA_GLOW= "rgba(224,64,251,0.12)"
GREEN       = "#00e676"
GREEN_DIM   = "#00c853"
RED         = "#ff5252"
RED_DIM     = "#c62828"
YELLOW      = "#ffd740"
ORANGE      = "#ff9100"
BLUE        = "#448aff"

TEXT_BRIGHT = "#f0f4ff"
TEXT_PRI    = "#c8d0e8"
TEXT_SEC    = "#6b7a99"
TEXT_DIM    = "#3a4a6a"
TEXT_MUTED  = "#252f45"

BORDER_SUB  = "#1a2540"
BORDER_MID  = "#253050"
BORDER_HI   = "#2e3d60"

FONT_MONO = '"JetBrains Mono","Fira Code","Cascadia Code","Consolas",monospace'
FONT_UI   = '"Segoe UI","Inter","SF Pro Display",sans-serif'

RADIUS_SM = "4px"
RADIUS_MD = "8px"
RADIUS_LG = "14px"

QSS_MAIN = f"""
* {{ font-family: {FONT_UI}; font-size: 13px; color: {TEXT_PRI}; outline: none; border: none; }}
QMainWindow, QDialog {{ background: {BG_BASE}; }}
QWidget {{ background: transparent; color: {TEXT_PRI}; }}

#sidebar {{ background: {BG_SURFACE}; border-right: 1px solid {BORDER_SUB}; min-width: 260px; max-width: 280px; }}
#sidebar_header {{ background: {BG_VOID}; border-bottom: 1px solid {BORDER_SUB}; padding: 0 16px; min-height: 60px; max-height: 60px; }}

QTabWidget::pane {{ background: {BG_BASE}; border: none; border-top: 2px solid {BORDER_SUB}; }}
QTabBar {{ background: {BG_VOID}; border-bottom: 1px solid {BORDER_SUB}; }}
QTabBar::tab {{ background: transparent; color: {TEXT_SEC}; padding: 14px 22px; margin: 0; border: none; border-bottom: 2px solid transparent; font-size: 11px; letter-spacing: 1.5px; font-weight: 600; min-width: 90px; }}
QTabBar::tab:selected {{ color: {CYAN}; border-bottom: 2px solid {CYAN}; background: rgba(0,255,231,0.04); }}
QTabBar::tab:hover:!selected {{ color: {TEXT_PRI}; background: rgba(255,255,255,0.03); border-bottom: 2px solid {BORDER_MID}; }}

QPushButton {{ background: {BG_ELEVATED}; border: 1px solid {BORDER_MID}; color: {TEXT_PRI}; padding: 9px 20px; border-radius: {RADIUS_MD}; font-size: 12px; font-weight: 600; letter-spacing: 0.5px; min-height: 16px; }}
QPushButton:hover {{ border-color: {CYAN_DIM}; color: {CYAN}; }}
QPushButton:pressed {{ background: {CYAN_GLOW}; border-color: {CYAN}; }}
QPushButton:disabled {{ background: {BG_SURFACE}; color: {TEXT_DIM}; border-color: {BORDER_SUB}; }}
QPushButton#btn_primary {{ background: rgba(0,184,165,0.2); border: 1px solid {CYAN}; color: {CYAN}; }}
QPushButton#btn_primary:hover {{ background: rgba(0,184,165,0.35); }}
QPushButton#btn_danger {{ background: rgba(255,82,82,0.1); border: 1px solid {RED}; color: {RED}; }}
QPushButton#btn_danger:hover {{ background: rgba(255,82,82,0.2); }}
QPushButton#btn_success {{ background: rgba(0,230,118,0.1); border: 1px solid {GREEN}; color: {GREEN}; }}
QPushButton#btn_success:hover {{ background: rgba(0,230,118,0.2); }}
QPushButton#btn_accent {{ background: rgba(224,64,251,0.1); border: 1px solid {MAGENTA}; color: {MAGENTA}; }}
QPushButton#btn_accent:hover {{ background: rgba(224,64,251,0.2); }}
QPushButton#btn_ghost {{ background: transparent; border: 1px solid {BORDER_MID}; color: {TEXT_SEC}; }}
QPushButton#btn_ghost:hover {{ border-color: {CYAN_DIM}; color: {TEXT_PRI}; }}
QPushButton#btn_send {{ background: {CYAN}; border: none; color: {BG_VOID}; font-weight: 700; border-radius: 20px; padding: 10px 22px; font-size: 12px; letter-spacing: 1px; }}
QPushButton#btn_send:hover {{ background: #33ffee; }}
QPushButton#btn_send:pressed {{ background: #00cca0; }}
QPushButton#btn_send:disabled {{ background: {BG_ELEVATED}; color: {TEXT_DIM}; }}
QPushButton#btn_icon {{ background: transparent; border: none; color: {TEXT_SEC}; padding: 6px; border-radius: {RADIUS_SM}; min-width: 28px; min-height: 28px; font-size: 15px; }}
QPushButton#btn_icon:hover {{ background: {BG_ELEVATED}; color: {CYAN}; }}

QLineEdit, QTextEdit, QPlainTextEdit {{ background: {BG_ELEVATED}; border: 1px solid {BORDER_MID}; color: {TEXT_BRIGHT}; padding: 10px 14px; border-radius: {RADIUS_MD}; font-size: 13px; selection-background-color: {CYAN_DIM}; selection-color: {BG_VOID}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {CYAN}; }}

QListWidget, QTreeWidget {{ background: {BG_SURFACE}; border: 1px solid {BORDER_SUB}; border-radius: {RADIUS_MD}; color: {TEXT_PRI}; outline: none; padding: 4px; }}
QListWidget::item {{ padding: 10px 12px; border-radius: {RADIUS_SM}; margin: 1px 0; border: none; }}
QListWidget::item:selected {{ background: {CYAN_GLOW}; color: {CYAN}; border: 1px solid rgba(0,255,231,0.2); }}
QListWidget::item:hover:!selected {{ background: rgba(255,255,255,0.04); }}

QScrollBar:vertical {{ background: transparent; width: 5px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER_HI}; border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {CYAN_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
QScrollBar:horizontal {{ height: 5px; background: transparent; }}
QScrollBar::handle:horizontal {{ background: {BORDER_HI}; border-radius: 3px; min-width: 24px; }}

QLabel {{ background: transparent; color: {TEXT_PRI}; }}
QLabel#lbl_title {{ color: {CYAN}; font-size: 18px; font-weight: 700; letter-spacing: 3px; }}
QLabel#lbl_section {{ color: {TEXT_SEC}; font-size: 10px; letter-spacing: 2px; font-weight: 600; }}
QLabel#lbl_fingerprint {{ color: {MAGENTA}; font-family: {FONT_MONO}; font-size: 10px; letter-spacing: 1px; }}
QLabel#lbl_online  {{ color: {GREEN};    font-size: 11px; font-weight: 600; }}
QLabel#lbl_offline {{ color: {TEXT_DIM}; font-size: 11px; }}
QLabel#lbl_pending {{ color: {YELLOW};   font-size: 11px; font-weight: 600; }}
QLabel#lbl_error   {{ color: {RED};      font-size: 11px; }}

QGroupBox {{ background: {BG_SURFACE}; border: 1px solid {BORDER_SUB}; border-radius: {RADIUS_LG}; margin-top: 18px; padding: 14px 12px 12px 12px; font-size: 11px; font-weight: 600; letter-spacing: 1px; color: {TEXT_SEC}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; top: -1px; color: {CYAN}; background: {BG_SURFACE}; padding: 0 6px; }}

QComboBox {{ background: {BG_ELEVATED}; border: 1px solid {BORDER_MID}; color: {TEXT_PRI}; padding: 8px 12px; border-radius: {RADIUS_MD}; min-width: 80px; }}
QComboBox:hover {{ border-color: {CYAN_DIM}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox QAbstractItemView {{ background: {BG_CARD}; border: 1px solid {BORDER_MID}; color: {TEXT_PRI}; selection-background-color: {CYAN_GLOW}; selection-color: {CYAN}; border-radius: {RADIUS_MD}; padding: 4px; }}

QSpinBox {{ background: {BG_ELEVATED}; border: 1px solid {BORDER_MID}; color: {TEXT_PRI}; padding: 8px 10px; border-radius: {RADIUS_MD}; }}
QSpinBox:focus {{ border-color: {CYAN}; }}
QSpinBox::up-button, QSpinBox::down-button {{ background: {BORDER_SUB}; border: none; width: 18px; border-radius: 2px; }}

QProgressBar {{ background: {BG_ELEVATED}; border: none; border-radius: 4px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {CYAN},stop:1 {MAGENTA}); border-radius: 4px; }}

QTableWidget {{ background: {BG_SURFACE}; border: 1px solid {BORDER_SUB}; border-radius: {RADIUS_MD}; gridline-color: {BORDER_SUB}; color: {TEXT_PRI}; outline: none; }}
QTableWidget::item {{ padding: 8px; }}
QTableWidget::item:selected {{ background: {CYAN_GLOW}; color: {CYAN}; }}
QHeaderView::section {{ background: {BG_VOID}; color: {CYAN}; padding: 10px 12px; border: none; border-bottom: 1px solid {BORDER_MID}; font-size: 10px; letter-spacing: 1.5px; font-weight: 700; }}

QSplitter::handle {{ background: {BORDER_SUB}; width: 1px; height: 1px; }}
QSplitter::handle:hover {{ background: {CYAN_DIM}; }}

QToolTip {{ background: {BG_CARD}; color: {TEXT_PRI}; border: 1px solid {BORDER_MID}; padding: 6px 10px; border-radius: {RADIUS_SM}; font-size: 11px; }}
QStatusBar {{ background: {BG_VOID}; border-top: 1px solid {BORDER_SUB}; color: {TEXT_SEC}; font-size: 11px; padding: 0 8px; }}

QMenu {{ background: {BG_CARD}; border: 1px solid {BORDER_MID}; border-radius: {RADIUS_MD}; padding: 6px; }}
QMenu::item {{ padding: 8px 20px; border-radius: {RADIUS_SM}; color: {TEXT_PRI}; }}
QMenu::item:selected {{ background: {CYAN_GLOW}; color: {CYAN}; }}
QMenu::separator {{ height: 1px; background: {BORDER_SUB}; margin: 4px 8px; }}

QCheckBox {{ color: {TEXT_PRI}; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER_MID}; border-radius: 3px; background: {BG_ELEVATED}; }}
QCheckBox::indicator:checked {{ background: {CYAN}; border-color: {CYAN}; }}
"""


def status_color(status: str) -> str:
    return {"trusted": GREEN, "online": GREEN, "pending": YELLOW,
            "blocked": RED, "discovered": CYAN, "offline": TEXT_DIM}.get(status, TEXT_SEC)
