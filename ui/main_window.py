"""ui/main_window.py - Master orchestrator, wires all subsystems."""
import asyncio, json, uuid
from datetime import datetime
from typing import Dict, Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QTabWidget,
    QLabel, QApplication, QSystemTrayIcon, QMenu, QFrame
)
from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, pyqtSlot, QTimer
from PyQt6.QtGui import QColor

from ui.theme import (
    QSS_MAIN, BG_VOID, BG_SURFACE, BG_ELEVATED,
    CYAN, CYAN_DIM, CYAN_GLOW, MAGENTA, GREEN, RED, YELLOW,
    TEXT_BRIGHT, TEXT_PRI, TEXT_SEC, TEXT_DIM, BORDER_SUB, BORDER_MID
)
from ui.radar_view      import RadarView
from ui.chat_view       import ChatView
from ui.network_map     import NetworkMapView
from ui.capsule_view    import CapsuleManagerView
from ui.audit_view      import AuditLogView
from ui.peers_sidebar   import PeersSidebar
from ui.approval_dialog import ConnectionApprovalDialog


class SecurityBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame{{background:{BG_VOID};border-top:1px solid {BORDER_SUB};}}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6); layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        for text, color, tip in [
            ("🔑 ECDH-FS",    CYAN,    "Forward Secrecy — ECDH P-256 per session"),
            ("🔏 RSA-PSS",    MAGENTA, "Message signing — RSA-PSS-2048"),
            ("🔐 AES-256",    GREEN,   "AES-256-GCM encryption at rest"),
            ("◈ Gossip",      CYAN_DIM,"Gossip protocol — eventual consistency sync"),
            ("💣 SelfDestruct",YELLOW, "Per-message TTL self-destruct"),
        ]:
            pill = QLabel(text)
            r, g, b = self._rgb(color)
            pill.setStyleSheet(
                f"background:rgba({r},{g},{b},0.1);color:{color};"
                f"font-size:9px;font-weight:700;padding:3px 8px;"
                f"border-radius:10px;border:1px solid rgba({r},{g},{b},0.3);"
                f"letter-spacing:0.5px;")
            pill.setToolTip(tip); layout.addWidget(pill)
        layout.addStretch()
        self._node_lbl = QLabel("◉ STARTING")
        self._node_lbl.setStyleSheet(
            f"color:{YELLOW};font-size:10px;font-weight:700;letter-spacing:1px;")
        layout.addWidget(self._node_lbl)
        self._bw_lbl = QLabel("↑ 0 B/s  ↓ 0 B/s")
        self._bw_lbl.setStyleSheet(
            f"color:{TEXT_DIM};font-size:10px;font-family:monospace;margin-left:12px;")
        layout.addWidget(self._bw_lbl)

    def set_online(self, online: bool):
        if online:
            self._node_lbl.setText("◉ ONLINE")
            self._node_lbl.setStyleSheet(
                f"color:{GREEN};font-size:10px;font-weight:700;letter-spacing:1px;")
        else:
            self._node_lbl.setText("◉ OFFLINE")
            self._node_lbl.setStyleSheet(
                f"color:{RED};font-size:10px;font-weight:700;letter-spacing:1px;")

    @staticmethod
    def _rgb(hex_color):
        h = hex_color.lstrip("#")
        if len(h) < 6: return 128, 128, 128
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class MainWindow(QMainWindow):
    def __init__(self, app, loop, node, db, identity):
        super().__init__()
        self._app      = app
        self._loop     = loop
        self._node     = node
        self._db       = db
        self._identity = identity
        self._chats: Dict[str, ChatView] = {}
        self._active_peer: Optional[str] = None

        self._setup_window()
        self._setup_ui()
        self._setup_tray()
        self._wire_callbacks()
        self._load_initial()

        self._topo_timer = QTimer(self)
        self._topo_timer.timeout.connect(self._refresh_topo)
        self._topo_timer.start(3000)

    # ── Window ────────────────────────────────────────────────────────────────

    def _setup_window(self):
        self.setWindowTitle("PhantomLink  ◈  P2P Secure Mesh  v2")
        self.resize(1340, 820); self.setMinimumSize(1000, 660)
        self.setStyleSheet(QSS_MAIN)
        self.statusBar().hide()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        root.addWidget(self._make_top_bar())

        content = QWidget()
        cl = QHBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(0)

        self._sidebar = PeersSidebar()
        self._sidebar.set_my_identity(
            self._identity.name,
            self._identity.fingerprint,
            self._identity.device_id)
        self._sidebar.chat_requested.connect(self._open_chat)
        self._sidebar.connect_requested.connect(self._initiate_connect)
        self._sidebar.approve_requested.connect(self._on_approve)
        self._sidebar.reject_requested.connect(self._on_reject)
        cl.addWidget(self._sidebar)

        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)
        self._tabs.currentChanged.connect(self._on_tab_change)

        self._radar = RadarView()
        self._radar.peer_selected.connect(self._open_chat)
        self._tabs.addTab(self._radar, "  ◉  RADAR  ")

        self._chat_placeholder = self._make_placeholder()
        self._tabs.addTab(self._chat_placeholder, "  💬  CHAT  ")

        self._netmap = NetworkMapView()
        self._tabs.addTab(self._netmap, "  ⬡  NETWORK  ")

        self._capsule = CapsuleManagerView(self._db)
        self._tabs.addTab(self._capsule, "  🔐  CAPSULE  ")

        self._audit = AuditLogView(self._db)
        self._tabs.addTab(self._audit, "  📋  AUDIT  ")

        cl.addWidget(self._tabs, stretch=1)
        root.addWidget(content, stretch=1)

        self._sec_bar = SecurityBar()
        root.addWidget(self._sec_bar)

    def _make_top_bar(self) -> QFrame:
        bar = QFrame(); bar.setFixedHeight(52)
        bar.setStyleSheet(
            f"QFrame{{background:qlineargradient("
            f"x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {BG_VOID},stop:0.5 #0a1020,stop:1 {BG_VOID});"
            f"border-bottom:1px solid {BORDER_SUB};}}")
        hl = QHBoxLayout(bar); hl.setContentsMargins(20, 0, 20, 0); hl.setSpacing(16)
        logo = QLabel("◈  PhantomLink")
        logo.setStyleSheet(
            f"color:{CYAN};font-size:17px;font-weight:800;"
            f"letter-spacing:3px;font-family:'JetBrains Mono','Fira Code',monospace;")
        hl.addWidget(logo)
        ver = QLabel("v2.0")
        ver.setStyleSheet(
            f"background:rgba(0,255,231,0.1);color:{CYAN_DIM};"
            f"font-size:9px;font-weight:700;padding:2px 8px;"
            f"border-radius:8px;border:1px solid rgba(0,255,231,0.2);")
        hl.addWidget(ver); hl.addStretch()
        self._peer_pill = QLabel("Peers: 0")
        self._peer_pill.setStyleSheet(
            f"background:{BG_ELEVATED};color:{TEXT_SEC};font-size:10px;"
            f"padding:4px 12px;border-radius:12px;border:1px solid {BORDER_MID};")
        hl.addWidget(self._peer_pill)
        self._conn_pill = QLabel("Connected: 0")
        self._conn_pill.setStyleSheet(
            f"background:rgba(0,230,118,0.1);color:{GREEN};font-size:10px;"
            f"padding:4px 12px;border-radius:12px;"
            f"border:1px solid rgba(0,230,118,0.25);")
        hl.addWidget(self._conn_pill)
        dev = QLabel(f"ID: {self._identity.device_id[:12]}…")
        dev.setStyleSheet(
            f"color:{TEXT_DIM};font-size:10px;font-family:monospace;")
        hl.addWidget(dev)
        return bar

    def _make_placeholder(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{BG_VOID};")
        l = QVBoxLayout(w)
        l.setAlignment(Qt.AlignmentFlag.AlignCenter); l.setSpacing(16)
        icon = QLabel("◈")
        icon.setStyleSheet(f"color:{CYAN};font-size:48px;letter-spacing:4px;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter); l.addWidget(icon)
        t = QLabel("Select a peer to begin")
        t.setStyleSheet(f"color:{TEXT_SEC};font-size:16px;letter-spacing:2px;")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter); l.addWidget(t)
        h = QLabel(
            "Click a node on the Radar  ·  Click 💬 in the sidebar\n"
            "or click ⚡ to connect to a discovered peer")
        h.setStyleSheet(f"color:{TEXT_DIM};font-size:12px;line-height:1.8;")
        h.setAlignment(Qt.AlignmentFlag.AlignCenter); l.addWidget(h)
        pr = QHBoxLayout()
        pr.setAlignment(Qt.AlignmentFlag.AlignCenter); pr.setSpacing(8)
        for text, color in [
            ("🔑 Forward Secrecy", CYAN), ("🔏 Signed Messages", MAGENTA),
            ("🧅 Onion Routing", YELLOW), ("📎 File Transfer", GREEN),
            ("💣 Self-Destruct", RED)
        ]:
            p = QLabel(text)
            p.setStyleSheet(
                f"background:rgba(255,255,255,0.03);color:{color};"
                f"font-size:10px;font-weight:600;padding:5px 12px;"
                f"border-radius:14px;border:1px solid rgba(255,255,255,0.08);")
            pr.addWidget(p)
        l.addLayout(pr)
        return w

    # ── Tray ──────────────────────────────────────────────────────────────────

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable(): return
        self._tray = QSystemTrayIcon(self)
        self._tray.setToolTip("PhantomLink ◈ P2P Secure Mesh")
        menu = QMenu()
        menu.addAction("Show", self.show)
        menu.addAction("Hide", self.hide)
        menu.addSeparator()
        menu.addAction("Quit", self._app.quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda r: (self.show(), self.raise_())
            if r == QSystemTrayIcon.ActivationReason.Trigger
               and not self.isVisible() else self.hide())
        self._tray.show()

    def _notify(self, title: str, message: str):
        if hasattr(self, "_tray"):
            self._tray.showMessage(
                title, message,
                QSystemTrayIcon.MessageIcon.Information, 4000)

    # ── Callbacks (node → Qt thread via QueuedConnection) ─────────────────────

    def _wire_callbacks(self):
        def _q(slot):
            def cb(*args):
                data = json.dumps(list(args), default=str)
                QMetaObject.invokeMethod(
                    self, slot,
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, data))
            return cb

        self._node.on_peer_discovered    = _q("_s_discovered")
        self._node.on_peer_connected     = _q("_s_connected")
        self._node.on_peer_disconnected  = _q("_s_disconnected")
        self._node.on_message_received   = _q("_s_message")
        self._node.on_connection_request = _q("_s_conn_request")
        self._node.on_connection_response= _q("_s_conn_response")

    @pyqtSlot(str)
    def _s_discovered(self, data):
        args = json.loads(data); info = args[0]
        pid  = info.get("peer_id", ""); name = info.get("name", "Unknown")
        row  = self._db.get_peer(pid)
        self._radar.add_peer(pid, name, row["status"] if row else "discovered")
        self._sidebar.add_discovered_peer(info)
        self._sec_bar.set_online(True)
        self._audit.add_live_event("discovered", pid, f"ip={info.get('ip')}")
        self._update_pills()

    @pyqtSlot(str)
    def _s_connected(self, data):
        args = json.loads(data); pid = args[0]
        peer = self._db.get_peer(pid); name = peer["name"] if peer else pid[:8]
        self._radar.update_peer_status(pid, "trusted")
        self._sidebar.set_peer_connected(pid, True)   # also clears live_pending
        self._radar.set_connected(self._node.get_connected_peers())
        self._netmap.update_topology(
            self._db.get_all_peers(), self._node.get_connected_peers())
        if pid in self._chats:
            self._chats[pid].set_online(True)
            self._chats[pid].add_system_message(
                "⚡  Connected · session key established · syncing…", CYAN_DIM)
        self._audit.add_live_event("connection_established", pid)
        self._notify("PhantomLink", f"Connected to {name}")
        self._update_pills()

    @pyqtSlot(str)
    def _s_disconnected(self, data):
        args = json.loads(data); pid = args[0]
        self._sidebar.set_peer_connected(pid, False)
        self._radar.set_connected(self._node.get_connected_peers())
        if pid in self._chats:
            self._chats[pid].set_online(False)
            self._chats[pid].add_system_message("⚠  Peer disconnected")
        self._netmap.update_topology(
            self._db.get_all_peers(), self._node.get_connected_peers())
        self._audit.add_live_event("disconnected", pid)
        self._update_pills()

    @pyqtSlot(str)
    def _s_message(self, data):
        args = json.loads(data); msg = args[0]

        if msg.get("type") == "sync_complete":
            pid = msg.get("peer_id")
            if pid and pid in self._chats:
                self._load_chat_msgs(pid)
                self._chats[pid].add_system_message(
                    f"⟳  Synced {msg.get('count','?')} messages via gossip", CYAN_DIM)
            return

        if msg.get("type") == "is_typing":
            pid = msg.get("peer_id") or msg.get("sender_id")
            if pid and pid in self._chats: self._chats[pid].show_typing(True)
            return

        if msg.get("type") == "stopped_typing":
            pid = msg.get("peer_id") or msg.get("sender_id")
            if pid and pid in self._chats: self._chats[pid].show_typing(False)
            return

        if msg.get("type") == "read_receipt":
            return

        pid = msg.get("peer_id")
        if pid in self._chats:
            self._chats[pid].add_message(msg)
        else:
            idx = self._get_chat_idx()
            if idx >= 0:
                t = self._tabs.tabText(idx)
                if "●" not in t: self._tabs.setTabText(idx, t.strip() + " ●")
            self._notify("New Message",
                         f"From {msg.get('sender_id','?')[:8]}: "
                         f"{str(msg.get('content',''))[:50]}")

    @pyqtSlot(str)
    def _s_conn_request(self, data):
        """
        Called when a peer initiates a TCP connection and sends connection_request.
        node._pending[peer_id] is alive at this point.
        We mark it in sidebar as live_pending so ✓/✗ buttons appear.
        Dialog is shown as a shortcut — dismissing it does NOT reject;
        the sidebar buttons remain functional regardless.
        """
        args      = json.loads(data)
        peer_info = args[0]
        pid       = peer_info.get("sender_id") or peer_info.get("peer_id", "")
        if not pid: return

        # Mark as live pending in sidebar immediately
        self._sidebar.mark_peer_pending(pid, {
            "peer_id":     pid,
            "name":        peer_info.get("name", "Unknown"),
            "fingerprint": peer_info.get("fingerprint", ""),
            "status":      "pending",
        })
        self._radar.update_peer_status(pid, "pending")
        self._audit.add_live_event("connection_request", pid)
        self._notify("PhantomLink",
                     f"{peer_info.get('name','Unknown')} wants to connect — check sidebar")

        # Dialog is a convenience shortcut only.
        # Dismissing / auto-timeout does NOT call reject_connection.
        dlg = ConnectionApprovalDialog(peer_info, self)
        dlg.exec()
        if dlg.accepted_connection:
            self._on_approve(pid)
        # If dismissed: _pending stays alive; sidebar ✓/✗ still works.

    @pyqtSlot(str)
    def _s_conn_response(self, data):
        args = json.loads(data)
        pid  = args[0]; accepted = args[1] if len(args) > 1 else False
        if not accepted:
            self._audit.add_live_event("connection_rejected", pid, "remote rejected")

    # ── Approve / Reject ──────────────────────────────────────────────────────

    def _on_approve(self, peer_id: str):
        """
        Called from sidebar ✓ button OR dialog Accept.
        1. Clear live_pending in sidebar immediately (stops ✓/✗ re-appearing on refresh).
        2. Schedule approve_connection on asyncio loop.
        """
        # Immediately update sidebar in-memory — prevents _refresh_topo from
        # re-showing ✓/✗ before DB update propagates.
        self._sidebar.clear_live_pending(peer_id)
        asyncio.run_coroutine_threadsafe(
            self._node.approve_connection(peer_id), self._loop)
        self._audit.add_live_event("connection_approved", peer_id)

    def _on_reject(self, peer_id: str):
        """Called from sidebar ✗ button."""
        self._sidebar.clear_live_pending(peer_id)
        self._sidebar.remove_peer(peer_id)
        self._radar.remove_peer(peer_id)
        asyncio.run_coroutine_threadsafe(
            self._node.reject_connection(peer_id), self._loop)
        self._audit.add_live_event("connection_rejected", peer_id)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _open_chat(self, peer_id: str):
        peer = self._db.get_peer(peer_id)
        name = peer["name"] if peer else peer_id[:8]
        if peer_id not in self._chats:
            chat = ChatView(peer_id, name, my_id=self._identity.device_id)
            chat.send_requested.connect(self._handle_send)
            chat.send_file_requested.connect(self._handle_file)
            chat.typing_started.connect(self._handle_typing_start)
            chat.typing_stopped.connect(self._handle_typing_stop)
            self._chats[peer_id] = chat
            idx = self._tabs.indexOf(self._chat_placeholder)
            if idx >= 0: self._tabs.removeTab(idx)
            self._tabs.insertTab(1, chat, f"  💬  {name[:12]}  ")
            chat.set_online(self._node.is_connected(peer_id))
            self._load_chat_msgs(peer_id)
        self._active_peer = peer_id
        self._tabs.setCurrentIndex(1)
        self._tabs.setTabText(1, f"  💬  {name[:12]}  ")

    def _load_chat_msgs(self, peer_id: str):
        if peer_id not in self._chats: return
        rows = self._db.get_messages_for_peer(peer_id)
        self._chats[peer_id].load_messages(
            rows, decrypt_fn=self._node.decrypt_message_content)

    def _initiate_connect(self, peer_id: str, ip: str, port: int):
        asyncio.run_coroutine_threadsafe(
            self._node.connect_to_peer(ip, port), self._loop)
        self._audit.add_live_event(
            "connection_initiated", peer_id, f"ip={ip}:{port}")

    def _handle_send(self, peer_id: str, content: str):
        # Reaction shortcut
        if content == "" and peer_id.startswith("__react__"):
            parts = peer_id.split("__")
            if len(parts) >= 4:
                mid, emoji = parts[2], parts[3]
                conn = self._node._connections.get(self._active_peer)
                if conn:
                    asyncio.run_coroutine_threadsafe(
                        conn.send({"type": "reaction_add", "message_id": mid,
                                   "peer_id": self._identity.device_id,
                                   "emoji": emoji,
                                   "timestamp": datetime.utcnow().isoformat()}),
                        self._loop)
            return

        # Optimistic bubble
        mid = str(uuid.uuid4()); ts = datetime.utcnow().isoformat()
        msg = {"message_id": mid, "sender_id": self._identity.device_id,
               "peer_id": peer_id, "timestamp": ts, "content": content,
               "direction": "sent", "verified": True, "signed": True}
        if peer_id in self._chats: self._chats[peer_id].add_message(msg)
        asyncio.run_coroutine_threadsafe(
            self._node.send_message(peer_id, content), self._loop)

    def _handle_file(self, peer_id: str, file_path: str):
        conn = self._node._connections.get(peer_id)
        if not conn:
            if peer_id in self._chats:
                self._chats[peer_id].add_system_message(
                    "⚠  Must be connected to send files", RED)
            return
        if peer_id in self._chats:
            import os as _os
            self._chats[peer_id].add_system_message(
                f"📎  Sending {_os.path.basename(file_path)}…", CYAN_DIM)

    def _handle_typing_start(self, peer_id: str):
        conn = self._node._connections.get(peer_id)
        if conn:
            asyncio.run_coroutine_threadsafe(
                conn.send({"type": "is_typing",
                           "sender_id": self._identity.device_id}), self._loop)

    def _handle_typing_stop(self, peer_id: str):
        conn = self._node._connections.get(peer_id)
        if conn:
            asyncio.run_coroutine_threadsafe(
                conn.send({"type": "stopped_typing",
                           "sender_id": self._identity.device_id}), self._loop)

    def _on_tab_change(self, idx: int):
        text = self._tabs.tabText(idx)
        if "●" in text:
            self._tabs.setTabText(idx, text.replace("●", "").strip() + "  ")

    # ── Refresh helpers ───────────────────────────────────────────────────────

    def _load_initial(self):
        peers = self._db.get_all_peers()
        for p in peers:
            self._radar.add_peer(p["peer_id"], p["name"], p["status"])
        self._sidebar.update_peers(peers, [], set())
        self._netmap.update_topology(peers, [])
        self._update_pills()

    def _refresh_topo(self):
        peers     = self._db.get_all_peers()
        connected = self._node.get_connected_peers()
        # Pass live_pending_ids so sidebar doesn't wipe in-flight requests
        live      = self._node.get_pending_peer_ids()
        self._netmap.update_topology(peers, connected)
        self._sidebar.update_peers(peers, connected, live_pending_ids=live)
        self._radar.set_connected(connected)
        self._update_pills()

    def _update_pills(self):
        peers = self._db.get_all_peers()
        conn  = self._node.get_connected_peers()
        self._peer_pill.setText(f"Peers: {len(peers)}")
        self._conn_pill.setText(f"Connected: {len(conn)}")

    def _get_chat_idx(self) -> int:
        for i in range(self._tabs.count()):
            if "CHAT" in self._tabs.tabText(i).upper(): return i
        return -1

    def closeEvent(self, event):
        self._topo_timer.stop()
        if hasattr(self, "_tray"): self._tray.hide()
        super().closeEvent(event)
