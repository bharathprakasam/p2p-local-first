"""ui/chat_view.py - Chat bubbles: typing, receipts, reactions, self-destruct, file offers."""
from datetime import datetime
from typing import Dict, Optional, List
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QLineEdit, QPushButton, QFrame, QSizePolicy, QSpacerItem,
    QFileDialog, QMenu, QApplication, QSpinBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint
from PyQt6.QtGui import QColor, QPixmap

from ui.theme import (
    BG_BASE, BG_SURFACE, BG_CARD, BG_ELEVATED, BG_VOID,
    CYAN, CYAN_DIM, CYAN_GLOW, MAGENTA, GREEN, RED, YELLOW,
    TEXT_BRIGHT, TEXT_PRI, TEXT_SEC, TEXT_DIM,
    BORDER_SUB, BORDER_MID, FONT_MONO
)

EMOJIS = ["👍","❤️","😂","😮","😢","🔥","✅","🎉","🤔","👀","🚀","💯"]


class TypingDots(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._f = 0
        self._t = QTimer(self); self._t.timeout.connect(self._tick)
        self.setStyleSheet(f"color:{CYAN};font-size:18px;letter-spacing:3px;")
        self.setText("   "); self.setFixedHeight(22)

    def start(self): self._t.start(400); self.show()
    def stop(self):  self._t.stop();  self.hide()
    def _tick(self):
        self.setText(["·  ","·· ","···"][self._f % 3]); self._f += 1


class SelfDestructLabel(QLabel):
    def __init__(self, seconds, parent=None):
        super().__init__(parent)
        self._r = seconds
        t = QTimer(self); t.timeout.connect(self._tick); t.start(1000)
        self._update()

    def _tick(self):
        self._r -= 1; self._update()

    def _update(self):
        if self._r > 0:
            m,s = divmod(self._r, 60)
            ts  = f"{m}:{s:02d}" if m else f"{s}s"
            c   = RED if self._r < 30 else YELLOW
            self.setText(f"💣 {ts}"); self.setStyleSheet(f"color:{c};font-size:10px;font-weight:bold;")
        else:
            self.setText("💣 gone"); self.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;")


class MessageBubble(QFrame):
    react_requested  = pyqtSignal(str, str)
    reply_requested  = pyqtSignal(dict)
    copy_requested   = pyqtSignal(str)
    file_accept_sig  = pyqtSignal(str)
    file_reject_sig  = pyqtSignal(str)

    def __init__(self, msg, direction, my_id="",
                 verified=True, signed=False, decryptable=True,
                 receipt_status="sent", ttl_remaining=None,
                 reactions=None, onion_routed=False, parent=None):
        super().__init__(parent)
        self._msg  = msg
        self._mid  = msg.get("message_id","")
        self._my_id = my_id
        self._direction = direction
        self._build(msg, direction, verified, signed, decryptable,
                    receipt_status, ttl_remaining, reactions or {}, onion_routed)

    def _build(self, msg, direction, verified, signed, decryptable,
               receipt, ttl, reactions, onion):
        is_sent = direction == "sent"
        outer   = QHBoxLayout(self)
        outer.setContentsMargins(12,3,12,3); outer.setSpacing(0)
        sp = QSpacerItem(50,1,QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Minimum)
        if is_sent: outer.addItem(sp)

        cont = QWidget(); cont.setMaximumWidth(520)
        cl   = QVBoxLayout(cont); cl.setContentsMargins(0,0,0,0); cl.setSpacing(2)

        # Reply preview
        rp = msg.get("reply_to")
        if rp:
            rf = QFrame()
            rf.setStyleSheet(f"background:rgba(0,255,231,0.06);border-left:3px solid {CYAN_DIM};border-radius:4px;")
            rl = QVBoxLayout(rf); rl.setContentsMargins(8,4,8,4)
            rn = QLabel(rp.get("sender_name","")[:16])
            rn.setStyleSheet(f"color:{CYAN_DIM};font-size:10px;font-weight:bold;")
            rc = QLabel(rp.get("content","")[:60]+"…")
            rc.setStyleSheet(f"color:{TEXT_SEC};font-size:11px;")
            rl.addWidget(rn); rl.addWidget(rc); cl.addWidget(rf)

        # Bubble
        bub = QFrame(); bub.setObjectName("msg_bubble")
        bl  = QVBoxLayout(bub); bl.setContentsMargins(14,10,14,10); bl.setSpacing(6)

        mtype   = msg.get("msg_type","text")
        content = msg.get("content","")

        if mtype == "file_offer":
            self._add_file(bl, msg)
        elif mtype == "image" and msg.get("thumbnail"):
            px = QPixmap(); px.loadFromData(msg["thumbnail"])
            if not px.isNull():
                il = QLabel()
                il.setPixmap(px.scaled(320,240,Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))
                il.setStyleSheet("border-radius:8px;"); bl.addWidget(il)
        elif not decryptable:
            lbl = QLabel("🔒  Key deleted — message unreadable")
            lbl.setStyleSheet(f"color:{TEXT_DIM};font-style:italic;font-size:12px;")
            bl.addWidget(lbl)
        else:
            lbl = QLabel(content); lbl.setWordWrap(True); lbl.setMaximumWidth(480)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lbl.setStyleSheet(f"color:{TEXT_BRIGHT};font-size:13px;line-height:1.6;background:transparent;")
            bl.addWidget(lbl)

        # Meta row
        meta = QHBoxLayout(); meta.setSpacing(6)
        ts_raw = msg.get("timestamp","")
        try:    ts = datetime.fromisoformat(ts_raw).strftime("%H:%M")
        except: ts = ts_raw[-5:] if ts_raw else ""
        tl = QLabel(ts); tl.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;font-family:monospace;")
        meta.addWidget(tl)

        if verified is True:
            iv = QLabel("✓"); iv.setStyleSheet(f"color:{GREEN};font-size:10px;font-weight:bold;"); iv.setToolTip("Integrity verified")
        elif verified is False:
            iv = QLabel("⚠ TAMPERED"); iv.setStyleSheet(f"color:{RED};font-size:10px;font-weight:bold;"); iv.setToolTip("Hash mismatch!")
        else:
            iv = QLabel("◌"); iv.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;")
        meta.addWidget(iv)

        if signed:
            sg = QLabel("✍"); sg.setStyleSheet(f"color:{CYAN_DIM};font-size:11px;"); sg.setToolTip("RSA-PSS signed"); meta.addWidget(sg)
        if onion:
            on = QLabel("🧅"); on.setStyleSheet("font-size:11px;"); on.setToolTip("Onion routed"); meta.addWidget(on)
        if ttl is not None:
            meta.addWidget(SelfDestructLabel(ttl))

        meta.addStretch()
        enc = QLabel("🔐"); enc.setStyleSheet("font-size:10px;"); enc.setToolTip("AES-256-GCM"); meta.addWidget(enc)

        if is_sent:
            rmap  = {"sent":"✓","delivered":"✓✓","read":"✓✓"}
            rcol  = {"sent":TEXT_DIM,"delivered":TEXT_SEC,"read":CYAN}.get(receipt,TEXT_DIM)
            rr    = QLabel(rmap.get(receipt,"✓"))
            rr.setStyleSheet(f"color:{rcol};font-size:10px;font-weight:bold;"); rr.setToolTip(receipt)
            meta.addWidget(rr)
        bl.addLayout(meta)

        if is_sent:
            bub.setStyleSheet("QFrame#msg_bubble{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #0d2035,stop:1 #0a1828);border:1px solid rgba(0,255,231,0.25);border-radius:16px;border-bottom-right-radius:4px;}")
        else:
            bub.setStyleSheet("QFrame#msg_bubble{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #150e28,stop:1 #0f0a20);border:1px solid rgba(224,64,251,0.2);border-radius:16px;border-bottom-left-radius:4px;}")
        cl.addWidget(bub)

        if reactions:
            rb = QFrame(); rbl = QHBoxLayout(rb); rbl.setContentsMargins(0,2,0,0); rbl.setSpacing(4)
            for emoji, voters in reactions.items():
                cnt = len(voters); mine = self._my_id in voters
                btn = QPushButton(f"{emoji} {cnt}"); btn.setFixedHeight(24)
                btn.setStyleSheet(f"QPushButton{{background:{'rgba(0,255,231,0.15)' if mine else BG_ELEVATED};border:1px solid {'rgba(0,255,231,0.4)' if mine else BORDER_MID};color:{'white' if mine else TEXT_SEC};border-radius:12px;padding:0 8px;font-size:11px;}}QPushButton:hover{{background:rgba(0,255,231,0.2);color:white;}}")
                btn.clicked.connect(lambda _,e=emoji: self.react_requested.emit(self._mid,e))
                rbl.addWidget(btn)
            rbl.addStretch(); cl.addWidget(rb)

        outer.addWidget(cont)
        if not is_sent: outer.addItem(sp)

    def _add_file(self, layout, msg):
        tid  = msg.get("transfer_id",""); fn = msg.get("filename","file"); size = msg.get("size",0)
        ssz  = f"{size/1048576:.1f} MB" if size>1048576 else f"{size/1024:.1f} KB"
        fr   = QFrame(); fr.setStyleSheet("QFrame{background:rgba(68,138,255,0.1);border:1px solid rgba(68,138,255,0.3);border-radius:8px;}")
        fl   = QVBoxLayout(fr)
        row  = QHBoxLayout()
        row.addWidget(QLabel("📎"))
        info = QVBoxLayout()
        nl   = QLabel(fn); nl.setStyleSheet(f"color:{TEXT_BRIGHT};font-weight:bold;font-size:12px;")
        sl   = QLabel(ssz); sl.setStyleSheet(f"color:{TEXT_SEC};font-size:11px;")
        info.addWidget(nl); info.addWidget(sl); row.addLayout(info); row.addStretch(); fl.addLayout(row)
        if self._direction == "received" and msg.get("offer_pending",True):
            br = QHBoxLayout()
            ab = QPushButton("↓  Accept"); ab.setObjectName("btn_success"); ab.setFixedHeight(30)
            ab.clicked.connect(lambda: self.file_accept_sig.emit(tid))
            rb = QPushButton("✕  Decline"); rb.setObjectName("btn_danger"); rb.setFixedHeight(30)
            rb.clicked.connect(lambda: self.file_reject_sig.emit(tid))
            br.addWidget(ab); br.addWidget(rb); fl.addLayout(br)
        layout.addWidget(fr)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("↩ Reply", lambda: self.reply_requested.emit(self._msg))
        em = menu.addMenu("😊 React")
        for e in EMOJIS:
            em.addAction(e, lambda _=None,em=e: self.react_requested.emit(self._mid,em))
        menu.addSeparator()
        menu.addAction("📋 Copy", lambda: self.copy_requested.emit(self._msg.get("content","")))
        menu.exec(event.globalPos())


class ChatView(QWidget):
    send_requested      = pyqtSignal(str, str)
    send_file_requested = pyqtSignal(str, str)
    typing_started      = pyqtSignal(str)
    typing_stopped      = pyqtSignal(str)

    def __init__(self, peer_id, peer_name, my_id="", parent=None):
        super().__init__(parent)
        self.peer_id   = peer_id
        self.peer_name = peer_name
        self._my_id    = my_id
        self._online   = False
        self._bubbles: Dict[str, MessageBubble] = {}
        self._reply_to = None
        self._typing_sent = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)

        # Header
        hdr = QFrame(); hdr.setFixedHeight(62)
        hdr.setStyleSheet(f"QFrame{{background:{BG_VOID};border-bottom:1px solid {BORDER_SUB};}}")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(20,0,16,0); hl.setSpacing(12)
        av = QLabel(self.peer_name[0].upper() if self.peer_name else "?")
        av.setFixedSize(38,38); av.setAlignment(Qt.AlignmentFlag.AlignCenter)
        av.setStyleSheet(f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {CYAN_DIM},stop:1 {MAGENTA});color:{BG_VOID};font-weight:800;font-size:16px;border-radius:19px;")
        hl.addWidget(av)
        nc = QVBoxLayout(); nc.setSpacing(2)
        self._name_lbl = QLabel(self.peer_name); self._name_lbl.setStyleSheet(f"color:{TEXT_BRIGHT};font-size:14px;font-weight:700;")
        self._stat_lbl = QLabel("● Offline");    self._stat_lbl.setObjectName("lbl_offline")
        nc.addWidget(self._name_lbl); nc.addWidget(self._stat_lbl); hl.addLayout(nc); hl.addStretch()
        for icon,tip,fn in [("🔍","Search",self._toggle_search),("📎","Send file",self._pick_file),("ℹ","Session info",self._show_info)]:
            b = QPushButton(icon); b.setObjectName("btn_icon"); b.setFixedSize(34,34); b.setToolTip(tip); b.clicked.connect(fn); hl.addWidget(b)
        self._sec = QLabel("🔑 FS+Sign")
        self._sec.setStyleSheet(f"background:rgba(0,255,231,0.1);color:{CYAN};font-size:9px;font-weight:bold;padding:3px 7px;border-radius:10px;border:1px solid rgba(0,255,231,0.2);")
        self._sec.setToolTip("ECDH Forward Secrecy + RSA-PSS Signing")
        hl.addWidget(self._sec); layout.addWidget(hdr)

        # Search bar
        self._search_bar = QFrame(); self._search_bar.setStyleSheet(f"QFrame{{background:{BG_SURFACE};border-bottom:1px solid {BORDER_SUB};}}")
        sl = QHBoxLayout(self._search_bar); sl.setContentsMargins(16,8,16,8)
        self._search_in = QLineEdit(); self._search_in.setPlaceholderText("Search messages…"); self._search_in.returnPressed.connect(self._do_search)
        sl.addWidget(self._search_in)
        csb = QPushButton("✕"); csb.setObjectName("btn_icon"); csb.setFixedSize(28,28); csb.clicked.connect(self._toggle_search); sl.addWidget(csb)
        self._search_bar.hide(); layout.addWidget(self._search_bar)

        # Typing bar
        self._typing_bar = QFrame(); self._typing_bar.setStyleSheet("background:transparent;"); self._typing_bar.setFixedHeight(26)
        tl = QHBoxLayout(self._typing_bar); tl.setContentsMargins(20,0,0,0)
        self._dots = TypingDots()
        self._tlbl = QLabel(f"{self.peer_name} is typing…"); self._tlbl.setStyleSheet(f"color:{TEXT_SEC};font-size:11px;")
        tl.addWidget(self._dots); tl.addWidget(self._tlbl); tl.addStretch()
        self._typing_bar.hide(); layout.addWidget(self._typing_bar)

        # Scroll area
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"background:{BG_BASE};border:none;")
        self._mc = QWidget(); self._mc.setStyleSheet(f"background:{BG_BASE};")
        self._ml = QVBoxLayout(self._mc); self._ml.setContentsMargins(0,16,0,16); self._ml.setSpacing(1); self._ml.addStretch()
        self._scroll.setWidget(self._mc); layout.addWidget(self._scroll, stretch=1)

        # Reply frame
        self._reply_frame = QFrame(); self._reply_frame.setStyleSheet(f"QFrame{{background:{BG_SURFACE};border-top:1px solid rgba(0,255,231,0.15);}}")
        rl = QHBoxLayout(self._reply_frame); rl.setContentsMargins(16,8,16,8)
        self._reply_lbl = QLabel(""); self._reply_lbl.setStyleSheet(f"color:{CYAN_DIM};font-size:11px;")
        rl.addWidget(self._reply_lbl); rl.addStretch()
        cr = QPushButton("✕"); cr.setObjectName("btn_icon"); cr.setFixedSize(24,24); cr.clicked.connect(self._cancel_reply); rl.addWidget(cr)
        self._reply_frame.hide(); layout.addWidget(self._reply_frame)

        # Input bar
        inf = QFrame(); inf.setStyleSheet(f"QFrame{{background:{BG_VOID};border-top:1px solid {BORDER_SUB};}}")
        il = QHBoxLayout(inf); il.setContentsMargins(16,12,16,12); il.setSpacing(10)
        em = QPushButton("😊"); em.setObjectName("btn_icon"); em.setFixedSize(36,36); em.clicked.connect(self._show_emoji); il.addWidget(em)
        self._input = QLineEdit(); self._input.setPlaceholderText("Encrypt & send…")
        self._input.returnPressed.connect(self._on_send); self._input.textChanged.connect(self._on_text_changed)
        self._input.setStyleSheet(f"QLineEdit{{background:{BG_ELEVATED};border:1px solid {BORDER_MID};color:{TEXT_BRIGHT};padding:10px 16px;border-radius:20px;font-size:13px;}}QLineEdit:focus{{border-color:{CYAN};}}")
        il.addWidget(self._input)
        self._sd_btn = QPushButton("💣"); self._sd_btn.setObjectName("btn_icon"); self._sd_btn.setFixedSize(36,36)
        self._sd_btn.setCheckable(True); self._sd_btn.setToolTip("Self-destruct timer"); self._sd_btn.clicked.connect(self._toggle_sd)
        il.addWidget(self._sd_btn)
        self._send_btn = QPushButton("Send"); self._send_btn.setObjectName("btn_send"); self._send_btn.setFixedSize(88,40); self._send_btn.clicked.connect(self._on_send)
        il.addWidget(self._send_btn); layout.addWidget(inf)

        # SD options
        self._sd_frame = QFrame(); self._sd_frame.setStyleSheet(f"QFrame{{background:{BG_SURFACE};border-top:1px solid {BORDER_SUB};}}")
        sdl = QHBoxLayout(self._sd_frame); sdl.setContentsMargins(16,8,16,8)
        sdl.addWidget(QLabel("💣  Destruct after:"))
        self._sd_spin = QSpinBox(); self._sd_spin.setRange(5,86400); self._sd_spin.setValue(30); self._sd_spin.setSuffix(" sec"); self._sd_spin.setFixedWidth(120)
        sdl.addWidget(self._sd_spin); sdl.addStretch()
        self._sd_frame.hide(); layout.addWidget(self._sd_frame)

    # ── Public API ──────────────────────────────────────────────────────────

    def set_online(self, online):
        self._online = online
        if online:
            self._stat_lbl.setText("● Online"); self._stat_lbl.setObjectName("lbl_online"); self._sec.show()
        else:
            self._stat_lbl.setText("● Offline"); self._stat_lbl.setObjectName("lbl_offline")
        self._stat_lbl.setStyle(self._stat_lbl.style())
        self._input.setEnabled(online); self._send_btn.setEnabled(online)

    def show_typing(self, is_typing):
        if is_typing: self._dots.start(); self._typing_bar.show()
        else:         self._dots.stop();  self._typing_bar.hide()

    def add_message(self, msg, decryptable=True):
        mid = msg.get("message_id","")
        if mid and mid in self._bubbles: return
        b = MessageBubble(
            msg, msg.get("direction","received"), self._my_id,
            verified=msg.get("verified",None), signed=msg.get("signed",False),
            decryptable=decryptable, receipt_status=msg.get("receipt_status","sent"),
            ttl_remaining=msg.get("ttl_remaining"), reactions=msg.get("reactions",{}),
            onion_routed=msg.get("onion_routed",False)
        )
        b.react_requested.connect(lambda m,e: self.send_requested.emit(f"__react__{m}__{e}",""))
        b.reply_requested.connect(self._set_reply)
        b.copy_requested.connect(lambda t: QApplication.clipboard().setText(t))
        if mid: self._bubbles[mid] = b
        self._ml.insertWidget(self._ml.count()-1, b)
        QTimer.singleShot(50, self._scroll_bottom)

    def load_messages(self, messages, decrypt_fn=None):
        while self._ml.count() > 1:
            item = self._ml.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._bubbles.clear()
        for row in messages:
            content = row["content"] if hasattr(row,"__getitem__") else b""
            nonce   = row["nonce"]   if hasattr(row,"__getitem__") else b""
            ok = True
            if decrypt_fn:
                dec = decrypt_fn(self.peer_id, content, nonce)
                if dec is None: ok = False; text = "[key deleted]"
                else: text = dec
            else:
                text = content.decode("utf-8",errors="replace") if isinstance(content,bytes) else str(content)
            self.add_message({
                "message_id": row["message_id"] if hasattr(row,"__getitem__") else "",
                "sender_id":  row["sender_id"]  if hasattr(row,"__getitem__") else "",
                "timestamp":  row["timestamp"]   if hasattr(row,"__getitem__") else "",
                "content": text,
                "direction": row["direction"]    if hasattr(row,"__getitem__") else "received",
                "hash": row["content_hash"]      if hasattr(row,"__getitem__") else "",
                "verified": True,
            }, ok)

    def add_system_message(self, text, color=None):
        lbl = QLabel(text); lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color:{color or '#3a4a6a'};font-size:10px;letter-spacing:1px;padding:8px 0;background:transparent;")
        self._ml.insertWidget(self._ml.count()-1, lbl)

    def update_receipt(self, message_id, status): pass  # bubble receipt update

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_send(self):
        text = self._input.text().strip()
        if not text: return
        self._input.clear(); self._cancel_reply()
        self.send_requested.emit(self.peer_id, text); self._typing_sent = False

    def _on_text_changed(self, text):
        if text and not self._typing_sent:
            self._typing_sent = True; self.typing_started.emit(self.peer_id)
        elif not text and self._typing_sent:
            self._typing_sent = False; self.typing_stopped.emit(self.peer_id)

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self,"Select File to Send")
        if path: self.send_file_requested.emit(self.peer_id, path)

    def _toggle_search(self):
        if self._search_bar.isHidden(): self._search_bar.show(); self._search_in.setFocus()
        else: self._search_bar.hide()

    def _do_search(self):
        q = self._search_in.text().strip()
        if q: self.add_system_message(f"🔍  Searching: {q}…", CYAN_DIM)

    def _set_reply(self, msg):
        self._reply_to = msg
        self._reply_lbl.setText(f"↩  {msg.get('sender_id','')[:8]}: {msg.get('content','')[:50]}…")
        self._reply_frame.show(); self._input.setFocus()

    def _cancel_reply(self):
        self._reply_to = None; self._reply_frame.hide()

    def _toggle_sd(self):
        if self._sd_btn.isChecked(): self._sd_frame.show()
        else: self._sd_frame.hide()

    def _show_emoji(self):
        menu = QMenu(self)
        for e in EMOJIS:
            menu.addAction(e, lambda _=None,em=e: self._input.insert(em))
        menu.exec(self._input.mapToGlobal(QPoint(0,-menu.sizeHint().height())))

    def _show_info(self):
        self.add_system_message(
            "🔑 ECDH P-256 Forward Secrecy  ·  🔏 RSA-PSS-2048 Signing  ·  🔐 AES-256-GCM Storage", CYAN_DIM)

    def _scroll_bottom(self):
        b = self._scroll.verticalScrollBar(); b.setValue(b.maximum())
