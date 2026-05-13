"""ui/peers_sidebar.py"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QLineEdit, QMenu, QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal
from ui.theme import (
    BG_VOID, BG_SURFACE, BG_CARD, BG_ELEVATED,
    CYAN, CYAN_DIM, CYAN_GLOW, MAGENTA, GREEN, RED, YELLOW,
    TEXT_BRIGHT, TEXT_PRI, TEXT_SEC, TEXT_DIM,
    BORDER_SUB, BORDER_MID, FONT_MONO
)

GRADS = [(CYAN_DIM, MAGENTA), ("#00b8a5", "#0288d1"), (MAGENTA, "#7b1fa2"),
         ("#00897b", "#00acc1"), ("#5e35b1", "#1e88e5")]
STATUS_RING = {
    "trusted": GREEN, "online": GREEN, "pending": YELLOW,
    "blocked": RED, "discovered": CYAN, "offline": "transparent"
}


class PeerCard(QFrame):
    chat_requested    = pyqtSignal(str)
    connect_requested = pyqtSignal(str, str, int)
    info_requested    = pyqtSignal(str)
    approve_requested = pyqtSignal(str)
    reject_requested  = pyqtSignal(str)

    def __init__(self, peer: dict, connected: bool = False,
                 has_live_request: bool = False, parent=None):
        super().__init__(parent)
        self.peer_id          = peer.get("peer_id", "")
        self._peer            = peer
        self._connected       = connected
        self._has_live_request = has_live_request   # in node._pending right now
        self._build()

    def _build(self):
        status = self._peer.get("status", "discovered")
        conn   = self._connected
        ring   = STATUS_RING.get("online" if conn else status, "transparent")
        name   = self._peer.get("name", "Unknown")
        fp     = self._peer.get("fingerprint", "")
        ip     = self._peer.get("last_ip", "") or self._peer.get("ip", "")
        port   = int(self._peer.get("last_port", 47778) or
                     self._peer.get("port", 47778) or 47778)

        self.setObjectName("peer_card")
        self.setFixedHeight(76)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QFrame#peer_card{{
                background:{BG_CARD};border:1px solid {BORDER_SUB};
                border-left:3px solid {ring if ring!='transparent' else BORDER_SUB};
                border-radius:10px;margin:3px 6px;
            }}
            QFrame#peer_card:hover{{
                background:{BG_ELEVATED};
                border-left-color:{CYAN};border-color:{BORDER_MID};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        # Avatar
        av = QLabel(name[0].upper() if name else "?")
        av.setFixedSize(40, 40)
        av.setAlignment(Qt.AlignmentFlag.AlignCenter)
        g1, g2 = GRADS[abs(hash(self.peer_id)) % 5]
        av.setStyleSheet(
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f"stop:0 {g1},stop:1 {g2});"
            f"color:white;font-weight:800;font-size:15px;"
            f"border-radius:20px;border:2px solid {ring};"
        )
        layout.addWidget(av)

        # Info column
        info_col = QVBoxLayout(); info_col.setSpacing(3)
        nr = QHBoxLayout(); nr.setSpacing(6)
        nl = QLabel(name[:18] + ("…" if len(name) > 18 else ""))
        nl.setStyleSheet(f"color:{TEXT_BRIGHT};font-size:13px;font-weight:700;")
        nr.addWidget(nl)

        if conn:
            bt, bb, bf = "ONLINE",  "rgba(0,230,118,0.15)",  GREEN
        elif status == "pending" and self._has_live_request:
            bt, bb, bf = "INCOMING","rgba(255,215,64,0.15)",  YELLOW
        elif status == "pending":
            bt, bb, bf = "PENDING", "rgba(255,215,64,0.10)",  YELLOW
        elif status == "blocked":
            bt, bb, bf = "BLOCKED", "rgba(255,82,82,0.15)",   RED
        else:
            bt, bb, bf = status.upper()[:8], BG_ELEVATED, TEXT_DIM

        badge = QLabel(bt)
        badge.setStyleSheet(
            f"background:{bb};color:{bf};font-size:8px;font-weight:700;"
            f"letter-spacing:1px;padding:2px 6px;border-radius:8px;"
            f"border:1px solid {bf};"
        )
        nr.addWidget(badge); nr.addStretch()
        info_col.addLayout(nr)

        if fp:
            fl = QLabel(f"◈  {fp[:23]}…")
            fl.setStyleSheet(
                f"color:{MAGENTA};font-family:{FONT_MONO};font-size:9px;"
            )
            info_col.addWidget(fl)

        if ip:
            il2 = QLabel(f"⊹  {ip}")
            il2.setStyleSheet(
                f"color:{TEXT_DIM};font-size:9px;font-family:monospace;"
            )
            info_col.addWidget(il2)
        elif self._has_live_request:
            ht = QLabel("⚡ Wants to connect")
            ht.setStyleSheet(f"color:{YELLOW};font-size:9px;font-weight:600;")
            info_col.addWidget(ht)

        layout.addLayout(info_col)
        layout.addStretch()

        # Buttons
        bc = QVBoxLayout(); bc.setSpacing(4)

        if conn:
            cb = QPushButton("💬")
            cb.setObjectName("btn_icon"); cb.setFixedSize(30, 30)
            cb.setToolTip("Open chat")
            cb.clicked.connect(lambda: self.chat_requested.emit(self.peer_id))
            bc.addWidget(cb)

        elif self._has_live_request:
            # ── Live incoming request: ✓ Accept / ✗ Decline ──────────────────
            ab = QPushButton("✓")
            ab.setFixedSize(30, 30)
            ab.setToolTip("Accept connection")
            ab.setStyleSheet(
                f"QPushButton{{background:rgba(0,230,118,0.18);color:{GREEN};"
                f"border:1px solid {GREEN};border-radius:6px;font-size:14px;font-weight:900;}}"
                f"QPushButton:hover{{background:rgba(0,230,118,0.40);}}"
            )
            ab.clicked.connect(lambda: self.approve_requested.emit(self.peer_id))
            bc.addWidget(ab)

            rb = QPushButton("✗")
            rb.setFixedSize(30, 30)
            rb.setToolTip("Decline connection")
            rb.setStyleSheet(
                f"QPushButton{{background:rgba(255,82,82,0.12);color:{RED};"
                f"border:1px solid {RED};border-radius:6px;font-size:14px;font-weight:900;}}"
                f"QPushButton:hover{{background:rgba(255,82,82,0.35);}}"
            )
            rb.clicked.connect(lambda: self.reject_requested.emit(self.peer_id))
            bc.addWidget(rb)

        elif ip and status != "blocked":
            # ── Discovered / trusted not connected: ⚡ Connect ────────────────
            xb = QPushButton("⚡")
            xb.setObjectName("btn_ghost"); xb.setFixedSize(30, 30)
            xb.setToolTip("Connect")
            xb.clicked.connect(
                lambda: self.connect_requested.emit(self.peer_id, ip, port))
            bc.addWidget(xb)

        # Always: ⋯ info menu
        mb = QPushButton("⋯")
        mb.setObjectName("btn_icon"); mb.setFixedSize(30, 30)
        mb.clicked.connect(lambda: self.info_requested.emit(self.peer_id))
        bc.addWidget(mb)

        layout.addLayout(bc)

    def mouseDoubleClickEvent(self, event):
        if self._connected:
            self.chat_requested.emit(self.peer_id)


class PeersSidebar(QWidget):
    chat_requested    = pyqtSignal(str)
    connect_requested = pyqtSignal(str, str, int)
    approve_requested = pyqtSignal(str)
    reject_requested  = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self._peers: dict      = {}
        self._connected: set   = set()
        # IDs that have a LIVE connection request in node._pending right now.
        # Separate from DB "pending" status (which persists across restarts).
        self._live_pending: set = set()
        self._current_tab      = "All"
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # Header
        hdr = QFrame(); hdr.setObjectName("sidebar_header")
        hl  = QHBoxLayout(hdr); hl.setContentsMargins(16, 0, 12, 0)
        lo  = QLabel("◈")
        lo.setStyleSheet(
            f"color:{CYAN};font-size:20px;font-weight:bold;")
        hl.addWidget(lo)
        tl = QLabel("PEERS")
        tl.setStyleSheet(
            f"color:{CYAN};font-size:13px;font-weight:700;letter-spacing:3px;")
        hl.addWidget(tl); hl.addStretch()
        self._count = QLabel("0")
        self._count.setStyleSheet(
            f"background:{CYAN_GLOW};color:{CYAN};font-size:10px;font-weight:700;"
            f"padding:2px 8px;border-radius:10px;border:1px solid rgba(0,255,231,0.2);")
        hl.addWidget(self._count); layout.addWidget(hdr)

        # Search
        sf = QFrame()
        sf.setStyleSheet(
            f"background:{BG_SURFACE};border-bottom:1px solid {BORDER_SUB};")
        sl = QHBoxLayout(sf); sl.setContentsMargins(10, 8, 10, 8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("  🔍  Filter peers…")
        self._search.textChanged.connect(self._filter)
        self._search.setStyleSheet(
            f"QLineEdit{{background:{BG_ELEVATED};border:1px solid {BORDER_SUB};"
            f"color:{TEXT_PRI};padding:7px 12px;border-radius:16px;font-size:12px;}}"
            f"QLineEdit:focus{{border-color:{CYAN};}}")
        sl.addWidget(self._search); layout.addWidget(sf)

        # Filter tabs
        tf  = QFrame()
        tf.setStyleSheet(
            f"background:{BG_SURFACE};border-bottom:1px solid {BORDER_SUB};")
        tfl = QHBoxLayout(tf); tfl.setContentsMargins(8, 5, 8, 5); tfl.setSpacing(4)
        self._tabs = {}
        for label in ["All", "Online", "Trusted", "Pending"]:
            btn = QPushButton(label); btn.setCheckable(True); btn.setFixedHeight(26)
            btn.setStyleSheet(
                f"QPushButton{{background:transparent;border:1px solid {BORDER_SUB};"
                f"color:{TEXT_SEC};border-radius:13px;font-size:10px;"
                f"padding:0 10px;font-weight:600;}}"
                f"QPushButton:checked{{background:{CYAN_GLOW};"
                f"border-color:{CYAN};color:{CYAN};}}"
                f"QPushButton:hover:!checked{{background:{BG_ELEVATED};"
                f"color:{TEXT_PRI};}}")
            btn.clicked.connect(lambda _, l=label: self._filter_tab(l))
            self._tabs[label] = btn; tfl.addWidget(btn)
        self._tabs["All"].setChecked(True); layout.addWidget(tf)

        # Peer list (scrollable)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"background:{BG_SURFACE};border:none;")
        self._lw = QWidget(); self._lw.setStyleSheet(f"background:{BG_SURFACE};")
        self._ll = QVBoxLayout(self._lw)
        self._ll.setContentsMargins(0, 6, 0, 6)
        self._ll.setSpacing(0)
        self._ll.addStretch()
        scroll.setWidget(self._lw); layout.addWidget(scroll, stretch=1)

        # Footer — my identity
        idf = QFrame()
        idf.setStyleSheet(
            f"QFrame{{background:{BG_VOID};border-top:1px solid {BORDER_SUB};}}")
        idl = QVBoxLayout(idf); idl.setContentsMargins(14, 10, 14, 10); idl.setSpacing(4)
        mr  = QHBoxLayout()
        mav = QLabel("◈"); mav.setFixedSize(32, 32)
        mav.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mav.setStyleSheet(
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f"stop:0 {CYAN_DIM},stop:1 {MAGENTA});"
            f"color:{BG_VOID};font-weight:900;font-size:14px;border-radius:16px;")
        mr.addWidget(mav)
        mi = QVBoxLayout(); mi.setSpacing(2)
        self._my_name = QLabel("MY DEVICE")
        self._my_name.setStyleSheet(
            f"color:{TEXT_BRIGHT};font-size:12px;font-weight:700;")
        self._my_fp = QLabel("")
        self._my_fp.setStyleSheet(
            f"color:{MAGENTA};font-family:{FONT_MONO};font-size:8px;")
        mi.addWidget(self._my_name); mi.addWidget(self._my_fp)
        mr.addLayout(mi); mr.addStretch()
        dot = QLabel("● ONLINE")
        dot.setStyleSheet(f"color:{GREEN};font-size:9px;font-weight:700;")
        mr.addWidget(dot); idl.addLayout(mr); layout.addWidget(idf)

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_my_identity(self, name, fingerprint, device_id):
        self._my_name.setText(name[:20])
        self._my_fp.setText(fingerprint[:32] + "…")
        self._my_fp.setToolTip(f"ID: {device_id}\nFP: {fingerprint}")

    def update_peers(self, peers, connected_ids, live_pending_ids: set = None):
        """Called by _refresh_topo every 3s. Preserves live_pending state."""
        self._connected = set(connected_ids)
        if live_pending_ids is not None:
            self._live_pending = set(live_pending_ids)
        self._peers = {}
        for p in peers:
            pid = p["peer_id"] if hasattr(p, "__getitem__") else p.get("peer_id")
            if pid:
                self._peers[pid] = dict(p) if hasattr(p, "keys") else p
        self._rebuild()

    def add_discovered_peer(self, info: dict):
        pid = info.get("peer_id")
        if pid:
            self._peers[pid] = {**self._peers.get(pid, {}), **info}
            self._rebuild()

    def mark_peer_pending(self, peer_id: str, info: dict = None):
        """Call when a LIVE incoming connection_request arrives."""
        if info:
            self._peers[peer_id] = {**self._peers.get(peer_id, {}), **info}
        self._live_pending.add(peer_id)
        if peer_id in self._peers:
            self._peers[peer_id]["status"] = "pending"
        self._rebuild()

    def clear_live_pending(self, peer_id: str):
        """Call after approve OR reject — stops showing ✓/✗ immediately."""
        self._live_pending.discard(peer_id)
        # Don't wait for DB refresh — update in-memory status now
        if peer_id in self._peers:
            self._peers[peer_id]["status"] = "trusted"
        self._rebuild()

    def set_peer_connected(self, peer_id: str, connected: bool):
        self._live_pending.discard(peer_id)
        if connected:
            self._connected.add(peer_id)
            if peer_id in self._peers:
                self._peers[peer_id]["status"] = "trusted"
        else:
            self._connected.discard(peer_id)
        self._rebuild()

    def remove_peer(self, peer_id: str):
        self._peers.pop(peer_id, None)
        self._connected.discard(peer_id)
        self._live_pending.discard(peer_id)
        self._rebuild()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _filter(self, text): self._rebuild(filter_text=text.lower())

    def _filter_tab(self, tab):
        self._current_tab = tab
        for l, b in self._tabs.items(): b.setChecked(l == tab)
        self._rebuild()

    def _rebuild(self, filter_text: str = ""):
        while self._ll.count() > 1:
            item = self._ll.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        filter_text = filter_text or self._search.text().lower()
        shown = 0

        for pid, peer in self._peers.items():
            name   = peer.get("name", "")
            status = peer.get("status", "discovered")
            conn   = pid in self._connected
            live   = pid in self._live_pending

            # Tab filter
            if self._current_tab == "Online"  and not conn:           continue
            if self._current_tab == "Trusted" and status != "trusted": continue
            if self._current_tab == "Pending" and not live:            continue
            if filter_text and (filter_text not in name.lower()
                                and filter_text not in pid.lower()):   continue

            card = PeerCard(peer, conn, has_live_request=live)
            card.chat_requested.connect(self.chat_requested)
            card.connect_requested.connect(self.connect_requested)
            card.info_requested.connect(self._show_menu)
            card.approve_requested.connect(self.approve_requested)
            card.reject_requested.connect(self.reject_requested)

            self._ll.insertWidget(self._ll.count() - 1, card)
            shown += 1

        self._count.setText(str(shown))
        if shown == 0:
            el = QLabel(
                "No peers found\n\nDevices on your network\nappear here automatically")
            el.setAlignment(Qt.AlignmentFlag.AlignCenter)
            el.setStyleSheet(
                f"color:{TEXT_DIM};font-size:11px;padding:30px;line-height:1.8;")
            self._ll.insertWidget(0, el)

    def _show_menu(self, peer_id: str):
        peer   = self._peers.get(peer_id, {})
        status = peer.get("status", "")
        live   = peer_id in self._live_pending
        menu   = QMenu(self)
        menu.addAction(f"ID: {peer_id[:16]}…").setEnabled(False)
        menu.addSeparator()
        if peer_id in self._connected:
            menu.addAction("💬  Open Chat",
                           lambda: self.chat_requested.emit(peer_id))
        if live:
            menu.addAction("✓  Accept Connection",
                           lambda: self.approve_requested.emit(peer_id))
            menu.addAction("✗  Decline Connection",
                           lambda: self.reject_requested.emit(peer_id))
        fp = peer.get("fingerprint", "")
        if fp:
            menu.addAction("◈  Copy Fingerprint",
                           lambda: QApplication.clipboard().setText(fp))
        menu.exec(self.cursor().pos())
