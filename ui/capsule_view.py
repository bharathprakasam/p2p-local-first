"""ui/capsule_view.py - Capsule export/import + Right to Forget."""
import os
from datetime import datetime, timedelta
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QFileDialog, QMessageBox, QGroupBox,
    QSpinBox, QComboBox, QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from ui.theme import (
    BG_SURFACE, BG_CARD, BG_ELEVATED, CYAN, CYAN_GLOW,
    MAGENTA, GREEN, RED, YELLOW, TEXT_BRIGHT, TEXT_PRI, TEXT_SEC, BORDER_SUB
)

# ── Shared constants ───────────────────────────────────────────────────────────
_INPUT_H  = 34
_BTN_H    = 38
_GRP_PAD  = (18, 18, 18, 18)
_GRP_GAP  = 12


def _lbl(text: str, color: str = TEXT_SEC, size: int = 11) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color}; font-size:{size}px;"
        "background:transparent; border:none;"
    )
    return w


def _group_style(accent: str = CYAN) -> str:
    return (
        f"QGroupBox {{"
        f"  color:{accent};"
        f"  font-size:12px; font-weight:bold; letter-spacing:1px;"
        f"  border:1px solid rgba(0,255,231,0.22);"
        f"  border-radius:8px; margin-top:16px; padding-top:4px;"
        f"}}"
        f"QGroupBox::title {{"
        f"  subcontrol-origin:margin; subcontrol-position:top left;"
        f"  left:12px; padding:0 6px;"
        f"}}"
    )


class CapsuleManagerView(QWidget):
    forget_key_requested = pyqtSignal(str, str)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._setup_ui()

    # ── Build UI ───────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scroll area – prevents any clipping regardless of window height
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }"
            "QScrollBar:vertical {"
            "  background:transparent; width:6px; margin:0;"
            "}"
            "QScrollBar::handle:vertical {"
            f"  background:{BORDER_SUB}; border-radius:3px; min-height:30px;"
            "}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
            "  height:0px;"
            "}"
        )

        content = QWidget()
        content.setStyleSheet("background:transparent;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(28, 24, 28, 32)
        cl.setSpacing(22)

        # Title
        title = QLabel("◈  CAPSULE MANAGER")
        title.setStyleSheet(
            f"color:{CYAN}; font-size:16px; font-weight:bold;"
            f"letter-spacing:4px; padding-bottom:10px;"
            f"border-bottom:1px solid {BORDER_SUB}; background:transparent;"
        )
        cl.addWidget(title)

        desc = QLabel(
            "Capsules are AES-256-GCM encrypted snapshots of your entire PhantomLink state.\n"
            "Includes all messages, device identities, keys and metadata."
        )
        desc.setStyleSheet(
            f"color:{TEXT_SEC}; font-size:11px; line-height:1.6;"
            "background:transparent; border:none;"
        )
        desc.setWordWrap(True)
        cl.addWidget(desc)

        cl.addWidget(self._build_stats_card())
        cl.addWidget(self._build_export_group())
        cl.addWidget(self._build_import_group())
        cl.addWidget(self._build_rtf_group())
        cl.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ── Sections ───────────────────────────────────────────────────────────────

    def _build_stats_card(self) -> QFrame:
        msgs  = self._db.fetchone("SELECT COUNT(*) as c FROM messages")
        peers = self._db.fetchone("SELECT COUNT(*) as c FROM peers")
        mc    = msgs["c"]  if msgs  else 0
        pc    = peers["c"] if peers else 0

        card = QFrame()
        card.setFixedHeight(80)
        card.setStyleSheet(
            f"QFrame {{ background:rgba(0,255,231,0.05);"
            f"border:1px solid {CYAN}; border-radius:10px; }}"
        )
        row = QHBoxLayout(card)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        for i, (icon, label, value, color) in enumerate([
            ("🔐", "Encryption", "AES-256-GCM",    GREEN),
            ("💬", "Messages",   str(mc),           CYAN),
            ("👥", "Peers",      str(pc),           MAGENTA),
            ("🔑", "Key Algo",   "RSA-2048+PBKDF2", YELLOW),
        ]):
            if i > 0:
                div = QFrame()
                div.setFrameShape(QFrame.Shape.VLine)
                div.setFixedWidth(1)
                div.setStyleSheet(f"background:{BORDER_SUB}; border:none;")
                row.addWidget(div)

            cell = QWidget()
            cell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            cell.setStyleSheet("background:transparent; border:none;")
            vl = QVBoxLayout(cell)
            vl.setContentsMargins(12, 10, 12, 10)
            vl.setSpacing(4)
            vl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            lbl_icon = QLabel(f"{icon}  {label}")
            lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_icon.setStyleSheet(
                f"color:{TEXT_SEC}; font-size:10px; letter-spacing:1px;"
                "background:transparent; border:none;"
            )
            lbl_val = QLabel(value)
            lbl_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_val.setStyleSheet(
                f"color:{color}; font-size:13px; font-weight:bold;"
                "background:transparent; border:none;"
            )
            vl.addWidget(lbl_icon)
            vl.addWidget(lbl_val)
            row.addWidget(cell)

        return card

    def _build_export_group(self) -> QGroupBox:
        grp = QGroupBox("Export Capsule")
        grp.setStyleSheet(_group_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(*_GRP_PAD)
        lay.setSpacing(_GRP_GAP)

        lay.addWidget(_lbl("Passphrase:"))

        pw_row = QHBoxLayout()
        pw_row.setSpacing(10)
        self._ex_pw = QLineEdit()
        self._ex_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._ex_pw.setFixedHeight(_INPUT_H)
        self._ex_pw.setPlaceholderText("Strong passphrase (min 12 chars)")
        self._ex_pw2 = QLineEdit()
        self._ex_pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self._ex_pw2.setFixedHeight(_INPUT_H)
        self._ex_pw2.setPlaceholderText("Confirm passphrase")
        pw_row.addWidget(self._ex_pw)
        pw_row.addWidget(self._ex_pw2)
        lay.addLayout(pw_row)

        eb = QPushButton("⬇  EXPORT CAPSULE")
        eb.setObjectName("btn_accent")
        eb.setFixedHeight(_BTN_H)
        eb.clicked.connect(self._do_export)
        lay.addWidget(eb)

        self._ex_status = QLabel("")
        self._ex_status.setStyleSheet(
            f"color:{GREEN}; font-size:11px; background:transparent; border:none;"
        )
        lay.addWidget(self._ex_status)
        return grp

    def _build_import_group(self) -> QGroupBox:
        grp = QGroupBox("Import Capsule")
        grp.setStyleSheet(_group_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(*_GRP_PAD)
        lay.setSpacing(_GRP_GAP)

        lay.addWidget(_lbl("Capsule file:"))

        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        self._im_path = QLineEdit()
        self._im_path.setPlaceholderText("Select capsule file…")
        self._im_path.setReadOnly(True)
        self._im_path.setFixedHeight(_INPUT_H)
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedWidth(84)
        browse_btn.setFixedHeight(_INPUT_H)
        browse_btn.clicked.connect(self._browse)
        file_row.addWidget(self._im_path)
        file_row.addWidget(browse_btn)
        lay.addLayout(file_row)

        lay.addWidget(_lbl("Passphrase:"))

        self._im_pw = QLineEdit()
        self._im_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._im_pw.setFixedHeight(_INPUT_H)
        self._im_pw.setPlaceholderText("Capsule passphrase")
        lay.addWidget(self._im_pw)

        ib = QPushButton("⬆  IMPORT CAPSULE")
        ib.setFixedHeight(_BTN_H)
        ib.clicked.connect(self._do_import)
        lay.addWidget(ib)

        self._im_status = QLabel("")
        self._im_status.setStyleSheet(
            f"color:{GREEN}; font-size:11px; background:transparent; border:none;"
        )
        lay.addWidget(self._im_status)
        return grp

    def _build_rtf_group(self) -> QGroupBox:
        grp = QGroupBox("Right to Forget — Key Expiry")
        grp.setStyleSheet(_group_style(MAGENTA))
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(*_GRP_PAD)
        lay.setSpacing(_GRP_GAP)

        rd = QLabel(
            "Schedule automatic deletion of a peer's encryption key.\n"
            "After deletion, all messages with that peer become permanently unreadable."
        )
        rd.setStyleSheet(
            f"color:{TEXT_SEC}; font-size:11px; background:transparent; border:none;"
        )
        rd.setWordWrap(True)
        lay.addWidget(rd)

        lay.addWidget(_lbl("Peer ID:"))
        self._rtf_peer = QLineEdit()
        self._rtf_peer.setPlaceholderText("Paste peer UUID…")
        self._rtf_peer.setFixedHeight(_INPUT_H)
        lay.addWidget(self._rtf_peer)

        fi_row = QHBoxLayout()
        fi_row.setSpacing(10)
        fi_lbl = _lbl("Forget in:")
        fi_lbl.setFixedWidth(70)
        fi_row.addWidget(fi_lbl)

        self._rtf_amount = QSpinBox()
        self._rtf_amount.setRange(1, 365)
        self._rtf_amount.setValue(24)
        self._rtf_amount.setFixedWidth(72)
        self._rtf_amount.setFixedHeight(_INPUT_H)
        fi_row.addWidget(self._rtf_amount)

        self._rtf_unit = QComboBox()
        self._rtf_unit.addItems(["hours", "days"])
        self._rtf_unit.setFixedWidth(86)
        self._rtf_unit.setFixedHeight(_INPUT_H)
        fi_row.addWidget(self._rtf_unit)

        fi_row.addStretch()

        sched_btn = QPushButton("⏱  SCHEDULE DELETION")
        sched_btn.setObjectName("btn_danger")
        sched_btn.setFixedWidth(200)
        sched_btn.setFixedHeight(_BTN_H)
        sched_btn.clicked.connect(self._schedule)
        fi_row.addWidget(sched_btn)
        lay.addLayout(fi_row)

        self._rtf_status = QLabel("")
        self._rtf_status.setStyleSheet(
            f"color:{YELLOW}; font-size:11px; background:transparent; border:none;"
        )
        lay.addWidget(self._rtf_status)
        return grp

    # ── Actions (logic unchanged) ──────────────────────────────────────────────

    def _do_export(self):
        pw  = self._ex_pw.text()
        pw2 = self._ex_pw2.text()
        if len(pw) < 12:
            self._ex_status.setStyleSheet(f"color:{RED}; font-size:11px; background:transparent; border:none;")
            self._ex_status.setText("✗ Passphrase too short (min 12 chars)")
            return
        if pw != pw2:
            self._ex_status.setStyleSheet(f"color:{RED}; font-size:11px; background:transparent; border:none;")
            self._ex_status.setText("✗ Passphrases do not match")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Capsule",
            f"phantomlink_{datetime.now():%Y%m%d_%H%M%S}.plcap",
            "PhantomLink Capsule (*.plcap);;All Files (*)"
        )
        if not path:
            return
        try:
            from crypto.capsule import export_capsule
            meta = export_capsule(self._db, pw, path)
            size = os.path.getsize(path) / 1024
            self._ex_status.setStyleSheet(f"color:{GREEN}; font-size:11px; background:transparent; border:none;")
            self._ex_status.setText(
                f"✓ Exported {meta['msg_count']} messages, {meta['peer_count']} peers"
                f" → {os.path.basename(path)} ({size:.1f} KB)"
            )
            self._ex_pw.clear()
            self._ex_pw2.clear()
        except Exception as e:
            self._ex_status.setStyleSheet(f"color:{RED}; font-size:11px; background:transparent; border:none;")
            self._ex_status.setText(f"✗ Export failed: {e}")

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Capsule", "",
            "PhantomLink Capsule (*.plcap);;All Files (*)"
        )
        if path:
            self._im_path.setText(path)

    def _do_import(self):
        path = self._im_path.text()
        pw   = self._im_pw.text()
        if not path or not os.path.exists(path):
            self._im_status.setStyleSheet(f"color:{RED}; font-size:11px; background:transparent; border:none;")
            self._im_status.setText("✗ Select a valid capsule file")
            return
        if not pw:
            self._im_status.setStyleSheet(f"color:{RED}; font-size:11px; background:transparent; border:none;")
            self._im_status.setText("✗ Enter passphrase")
            return
        if QMessageBox.question(
            self, "Confirm Import",
            "Import will merge capsule data into your current database.\n"
            "Existing records will not be overwritten.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            from crypto.capsule import import_capsule
            meta = import_capsule(self._db, pw, path)
            self._im_status.setStyleSheet(f"color:{GREEN}; font-size:11px; background:transparent; border:none;")
            self._im_status.setText(
                f"✓ Imported {meta.get('msg_count','?')} messages,"
                f" {meta.get('peer_count','?')} peers"
                f" from {meta.get('exported_at','?')}"
            )
            self._im_pw.clear()
        except ValueError as e:
            self._im_status.setStyleSheet(f"color:{RED}; font-size:11px; background:transparent; border:none;")
            self._im_status.setText(f"✗ {e}")
        except Exception as e:
            self._im_status.setStyleSheet(f"color:{RED}; font-size:11px; background:transparent; border:none;")
            self._im_status.setText(f"✗ Import error: {e}")

    def _schedule(self):
        peer_id = self._rtf_peer.text().strip()
        if not peer_id:
            self._rtf_status.setText("✗ Enter peer ID")
            return
        amount     = self._rtf_amount.value()
        unit       = self._rtf_unit.currentText()
        delta      = timedelta(hours=amount) if unit == "hours" else timedelta(days=amount)
        expires_at = (datetime.utcnow() + delta).isoformat()
        self._db.set_key_expiry(peer_id, expires_at)
        self._db.log_event("key_expiry_set", peer_id, f'{{"expires_at":"{expires_at}"}}')
        self._rtf_status.setText(
            f"✓ Key for {peer_id[:16]}… deleted in {amount} {unit}"
            f" ({expires_at[:16]} UTC)"
        )
        self.forget_key_requested.emit(peer_id, expires_at)
