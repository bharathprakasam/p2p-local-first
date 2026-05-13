"""ui/approval_dialog.py - Connection approval dialog with fingerprint."""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
)
from PyQt6.QtCore import Qt, QTimer
from ui.theme import (
    BG_BASE, BG_CARD, BG_ELEVATED, CYAN, CYAN_GLOW,
    MAGENTA, GREEN, RED, YELLOW, TEXT_BRIGHT, TEXT_PRI, TEXT_SEC, BORDER_MID, BORDER_SUB
)


class ConnectionApprovalDialog(QDialog):
    def __init__(self, peer_info: dict, parent=None):
        super().__init__(parent)
        self.peer_info = peer_info
        self.accepted_connection = False
        self._countdown = 60
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _setup_ui(self):
        self.setWindowTitle("⚡ Incoming Connection Request")
        self.setFixedSize(520, 480)
        self.setModal(True)
        self.setStyleSheet(f"QDialog{{background:{BG_BASE};border:1px solid {CYAN};}}")

        layout = QVBoxLayout(self); layout.setContentsMargins(24,24,24,24); layout.setSpacing(16)

        # Title
        title = QLabel("⚡  CONNECTION REQUEST")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color:{CYAN};font-size:16px;font-weight:bold;letter-spacing:4px;padding:8px;border-bottom:1px solid {CYAN};")
        layout.addWidget(title)

        # Warning
        warn = QLabel("⚠  VERIFY FINGERPRINT OUT-OF-BAND BEFORE ACCEPTING")
        warn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        warn.setStyleSheet(f"background:rgba(255,215,64,0.12);color:{YELLOW};font-size:10px;letter-spacing:1px;padding:7px;border:1px solid rgba(255,215,64,0.3);border-radius:6px;")
        layout.addWidget(warn)

        # Device info card
        card = QFrame()
        card.setStyleSheet(f"QFrame{{background:{BG_CARD};border:1px solid {BORDER_SUB};border-radius:10px;}}")
        cl = QVBoxLayout(card); cl.setSpacing(10); cl.setContentsMargins(16,14,16,14)

        name = self.peer_info.get("name","Unknown")
        pid  = self.peer_info.get("peer_id","")
        addr = self.peer_info.get("addr",("?","?"))

        for label, value, color in [
            ("Device Name", name, CYAN),
            ("Device ID",   (pid[:32]+"…" if len(pid)>32 else pid), TEXT_SEC),
            ("Address",     f"{addr[0]}:{addr[1]}", TEXT_SEC),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(f"{label}:")
            lbl.setStyleSheet(f"color:{TEXT_SEC};font-size:11px;min-width:110px;")
            val = QLabel(value)
            val.setStyleSheet(f"color:{color};font-size:11px;font-family:monospace;")
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(lbl); row.addWidget(val); row.addStretch()
            cl.addLayout(row)
        layout.addWidget(card)

        # Fingerprint
        fp_group = QFrame()
        fp_group.setStyleSheet(f"QFrame{{background:rgba(224,64,251,0.06);border:1px solid {MAGENTA};border-radius:8px;}}")
        fpl = QVBoxLayout(fp_group); fpl.setContentsMargins(16,12,16,12)
        fp_title = QLabel("◈  CRYPTOGRAPHIC FINGERPRINT  ◈")
        fp_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fp_title.setStyleSheet(f"color:{MAGENTA};font-size:10px;letter-spacing:2px;font-weight:700;")
        fpl.addWidget(fp_title)

        fingerprint = self.peer_info.get("fingerprint","N/A")
        chunks = fingerprint.split(":")
        for i in range(0, min(len(chunks),16), 8):
            line = "  ".join(chunks[i:i+8])
            lbl  = QLabel(line); lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color:{MAGENTA};font-size:13px;font-family:monospace;font-weight:bold;letter-spacing:2px;padding:4px;")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            fpl.addWidget(lbl)
        layout.addWidget(fp_group)

        # Buttons
        btn_row = QHBoxLayout(); btn_row.setSpacing(12)
        self._reject_btn = QPushButton("✕  REJECT")
        self._reject_btn.setObjectName("btn_danger"); self._reject_btn.setFixedHeight(44)
        self._reject_btn.clicked.connect(self._on_reject)
        btn_row.addWidget(self._reject_btn)

        self._cd_label = QLabel(f"Auto-reject in {self._countdown}s")
        self._cd_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cd_label.setStyleSheet(f"color:{TEXT_SEC};font-size:10px;")
        btn_row.addWidget(self._cd_label)

        self._accept_btn = QPushButton("✓  ACCEPT")
        self._accept_btn.setObjectName("btn_success"); self._accept_btn.setFixedHeight(44)
        self._accept_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(self._accept_btn)
        layout.addLayout(btn_row)

    def _tick(self):
        self._countdown -= 1
        self._cd_label.setText(f"Auto-reject in {self._countdown}s")
        if self._countdown <= 0: self._on_reject()

    def _on_accept(self):
        self._timer.stop(); self.accepted_connection = True; self.accept()

    def _on_reject(self):
        self._timer.stop(); self.accepted_connection = False; self.reject()
