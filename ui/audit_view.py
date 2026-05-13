"""ui/audit_view.py - Real-time audit log with color-coded events."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from ui.theme import (BG_BASE, BG_SURFACE, BG_VOID, CYAN, GREEN, RED,
                       YELLOW, MAGENTA, TEXT_PRI, TEXT_SEC, BORDER_SUB, BORDER_MID)

EVENT_COLORS = {
    "connection_request":     YELLOW,
    "connection_approved":    GREEN,
    "connection_rejected":    RED,
    "connection_established": GREEN,
    "connection_initiated":   CYAN,
    "disconnected":           RED,
    "sync_initiated":         CYAN,
    "sync_complete":          CYAN,
    "key_expiry_set":         MAGENTA,
    "key_deleted":            RED,
    "relay_stored":           YELLOW,
    "relay_delivered":        GREEN,
    "discovered":             CYAN,
    "group_message":          MAGENTA,
    "error":                  RED,
}


class AuditLogView(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._setup_ui()
        self._timer = QTimer(self); self._timer.timeout.connect(self.refresh); self._timer.start(5000)
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(16,16,16,16); layout.setSpacing(12)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("◈  AUDIT LOG")
        title.setStyleSheet(f"color:{CYAN};font-size:14px;font-weight:bold;letter-spacing:3px;")
        hdr.addWidget(title); hdr.addStretch()
        rb = QPushButton("↺  Refresh"); rb.setFixedWidth(110); rb.clicked.connect(self.refresh)
        hdr.addWidget(rb); layout.addLayout(hdr)

        # Stats row
        self._stats = QLabel("")
        self._stats.setStyleSheet(f"color:{TEXT_SEC};font-size:10px;"); layout.addWidget(self._stats)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Timestamp","Event","Peer ID","Details"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setStyleSheet(f"""
            QTableWidget{{background:{BG_SURFACE};gridline-color:{BORDER_SUB};border:1px solid {BORDER_SUB};}}
            QHeaderView::section{{background:{BG_VOID};color:{CYAN};padding:10px 12px;border:none;border-bottom:1px solid {BORDER_MID};font-size:10px;letter-spacing:1.5px;font-weight:700;}}
            QTableWidget::item{{padding:8px;}} QTableWidget::item:alternate{{background:#0d1525;}}
        """)
        layout.addWidget(self._table)

    def refresh(self):
        rows = self._db.get_audit_log(300)
        self._table.setRowCount(len(rows))
        font = QFont("JetBrains Mono", 9)
        for i, row in enumerate(rows):
            ts      = row["timestamp"][:19].replace("T"," ")
            event   = row["event_type"]
            peer    = row["peer_id"] or ""
            details = row["details"] or ""
            color   = QColor(EVENT_COLORS.get(event, TEXT_SEC))
            for j, text in enumerate([ts, event, peer[:24], details[:80]]):
                item = QTableWidgetItem(text); item.setFont(font)
                item.setForeground(color if j==1 else QColor(TEXT_SEC if j!=0 else TEXT_PRI))
                self._table.setItem(i, j, item)
        self._table.scrollToBottom()
        self._stats.setText(f"Total events: {len(rows)}  ·  Auto-refreshes every 5s")

    def add_live_event(self, event_type, peer_id="", details=""):
        from datetime import datetime
        row = self._table.rowCount(); self._table.insertRow(row)
        font  = QFont("JetBrains Mono", 9)
        color = QColor(EVENT_COLORS.get(event_type, TEXT_SEC))
        ts    = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        for j, text in enumerate([ts, event_type, peer_id[:24], details[:80]]):
            item = QTableWidgetItem(text); item.setFont(font)
            item.setForeground(color if j==1 else QColor(TEXT_SEC))
            self._table.setItem(row, j, item)
        self._table.scrollToBottom()
