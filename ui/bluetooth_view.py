"""
BluetoothView — UI panel for Bluetooth peer discovery and connection.
Shows: BLE scan results, paired devices, RFCOMM connection status,
send message over BT, transport indicator in chat.
"""

import asyncio
from typing import Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFrame, QGroupBox, QProgressBar,
    QTextEdit, QSplitter
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon

from ui.theme import (
    BG_BASE, BG_SURFACE, BG_CARD, BG_ELEVATED, BG_VOID,
    CYAN, CYAN_DIM, CYAN_GLOW, MAGENTA, MAGENTA_GLOW,
    GREEN, GREEN_DIM, RED, YELLOW, ORANGE, BLUE,
    TEXT_BRIGHT, TEXT_PRI, TEXT_SEC, TEXT_DIM,
    BORDER_SUB, BORDER_MID, FONT_MONO, status_color
)


class BluetoothView(QWidget):
    """
    Bluetooth management panel.
    connect_requested(bt_address, peer_id) → caller handles RFCOMM connect.
    """
    connect_requested = pyqtSignal(str, str)   # (bt_address, peer_id)

    def __init__(self, loop: asyncio.AbstractEventLoop,
                 bt_manager=None, parent=None):
        super().__init__(parent)
        self._loop = loop
        self._bt = bt_manager   # BluetoothManager instance (None if BT unavailable)
        self._scanning = False
        self._setup_ui()
        self._check_bt_status()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("◈  BLUETOOTH MESH")
        title.setStyleSheet(f"""
            color: {MAGENTA};
            font-size: 16px;
            font-weight: bold;
            letter-spacing: 4px;
        """)
        hdr.addWidget(title)
        hdr.addStretch()

        self._status_badge = QLabel("● CHECKING…")
        self._status_badge.setStyleSheet(f"color: {YELLOW}; font-size: 11px;")
        hdr.addWidget(self._status_badge)
        layout.addLayout(hdr)

        # ── Info card ─────────────────────────────────────────────────────────
        self._info_card = QFrame()
        self._info_card.setStyleSheet(f"""
            QFrame {{
                background: rgba(255,0,200,0.05);
                border: 1px solid {MAGENTA};
                border-radius: 8px;
                padding: 4px;
            }}
        """)
        info_layout = QHBoxLayout(self._info_card)

        for icon, label, val_attr in [
            ("📡", "BLE Scan", "_ble_status"),
            ("🔗", "RFCOMM", "_rfcomm_status"),
            ("📶", "Range", "_range_label"),
        ]:
            box = QFrame()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(12, 6, 12, 6)
            i_lbl = QLabel(f"{icon}  {label}")
            i_lbl.setStyleSheet(f"color: {TEXT_SEC}; font-size: 10px;")
            v_lbl = QLabel("—")
            v_lbl.setStyleSheet(f"color: {MAGENTA}; font-size: 11px; font-weight: bold;")
            setattr(self, val_attr, v_lbl)
            bl.addWidget(i_lbl)
            bl.addWidget(v_lbl)
            info_layout.addWidget(box)
        layout.addWidget(self._info_card)

        # ── Splitter: discovered | log ─────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: device list
        left = QFrame()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)

        # BLE Scan group
        ble_group = QGroupBox("BLE Discovered Devices")
        ble_layout = QVBoxLayout(ble_group)

        scan_row = QHBoxLayout()
        self._scan_btn = QPushButton("⟳  START BLE SCAN")
        self._scan_btn.setObjectName("magenta")
        self._scan_btn.clicked.connect(self._toggle_scan)
        scan_row.addWidget(self._scan_btn)

        self._scan_progress = QProgressBar()
        self._scan_progress.setRange(0, 0)   # indeterminate
        self._scan_progress.setVisible(False)
        self._scan_progress.setFixedHeight(6)
        scan_row.addWidget(self._scan_progress)
        ble_layout.addLayout(scan_row)

        self._ble_list = QListWidget()
        self._ble_list.setStyleSheet(f"""
            QListWidget {{
                background: {BG_SURFACE};
                border: 1px solid {BORDER_MID};
                border-radius: 4px;
            }}
            QListWidget::item {{
                padding: 8px;
                border-bottom: 1px solid {BORDER_MID};
                color: {TEXT_PRI};
            }}
            QListWidget::item:selected {{
                background: rgba(255,0,200,0.15);
                color: {MAGENTA};
            }}
        """)
        self._ble_list.setMinimumHeight(180)
        ble_layout.addWidget(self._ble_list)

        connect_btn = QPushButton("⚡  CONNECT SELECTED")
        connect_btn.setObjectName("magenta")
        connect_btn.clicked.connect(self._connect_selected)
        ble_layout.addWidget(connect_btn)
        ll.addWidget(ble_group)

        # Paired devices group
        paired_group = QGroupBox("Paired / Known BT Devices")
        paired_layout = QVBoxLayout(paired_group)

        scan_paired_btn = QPushButton("🔍  SCAN NEARBY DEVICES (Classic BT)")
        scan_paired_btn.clicked.connect(self._scan_classic)
        paired_layout.addWidget(scan_paired_btn)

        self._paired_list = QListWidget()
        self._paired_list.setStyleSheet(self._ble_list.styleSheet())
        self._paired_list.setMinimumHeight(120)
        paired_layout.addWidget(self._paired_list)
        ll.addWidget(paired_group)

        splitter.addWidget(left)

        # Right: activity log
        right = QFrame()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)

        log_label = QLabel("BT Activity Log")
        log_label.setStyleSheet(f"color: {TEXT_SEC}; font-size: 10px; letter-spacing: 2px;")
        rl.addWidget(log_label)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(f"""
            QTextEdit {{
                background: {BG_SURFACE};
                border: 1px solid {BORDER_MID};
                color: {TEXT_SEC};
                font-family: monospace;
                font-size: 10px;
                padding: 6px;
            }}
        """)
        rl.addWidget(self._log)

        splitter.addWidget(right)
        splitter.setSizes([400, 280])
        layout.addWidget(splitter, stretch=1)

        # ── How to use note ────────────────────────────────────────────────────
        note = QLabel(
            "💡  Both devices must have PhantomLink running.  "
            "BLE discovery is automatic.  RFCOMM requires PyBluez (Windows/Linux).  "
            "macOS supports BLE scan only."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"""
            color: {TEXT_SEC};
            font-size: 10px;
            background: rgba(0,255,231,0.04);
            border: 1px solid {BORDER_MID};
            border-radius: 4px;
            padding: 8px;
        """)
        layout.addWidget(note)

    # ── Bluetooth status check ────────────────────────────────────────────────

    def _check_bt_status(self):
        from networking.bluetooth_transport import BluetoothManager
        available, reason = BluetoothManager.check_bluetooth_available()

        if available:
            self._status_badge.setText("● AVAILABLE")
            self._status_badge.setStyleSheet(f"color: {GREEN}; font-size: 11px;")
            self._ble_status.setText("Ready")
            self._rfcomm_status.setText("Ready" if "RFCOMM" not in reason else "BLE only")
            self._range_label.setText("~30m BLE / ~100m RFCOMM")
            self._log_msg(f"✓ {reason}")
        else:
            self._status_badge.setText("● UNAVAILABLE")
            self._status_badge.setStyleSheet(f"color: {RED}; font-size: 11px;")
            self._scan_btn.setEnabled(False)
            self._log_msg(f"✗ {reason}")
            self._log_msg("Run: pip install bleak PyBluez")

    # ── BLE scan ──────────────────────────────────────────────────────────────

    def _toggle_scan(self):
        if not self._scanning:
            self._start_scan()
        else:
            self._stop_scan()

    def _start_scan(self):
        self._scanning = True
        self._scan_btn.setText("⏹  STOP SCAN")
        self._scan_progress.setVisible(True)
        self._ble_list.clear()
        self._log_msg("BLE scan started — looking for PhantomLink peers…")

        # Run BLE scan in asyncio loop
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._run_ble_scan(), self._loop
            )

    async def _run_ble_scan(self):
        try:
            from bleak import BleakScanner
            from networking.bluetooth_transport import PHANTOMLINK_BLE_UUID

            devices = await BleakScanner.discover(
                timeout=8.0,
                service_uuids=[PHANTOMLINK_BLE_UUID]
            )
            # Also show devices with PL: name prefix
            all_devices = await BleakScanner.discover(timeout=5.0)
            pl_devices = [d for d in all_devices
                          if d.name and d.name.startswith("PL:")]

            found = {d.address: d for d in (devices + pl_devices)}

            from PyQt6.QtCore import QMetaObject, Q_ARG, Qt
            import json
            data = json.dumps([
                {"address": d.address, "name": d.name or "Unknown",
                 "rssi": d.rssi}
                for d in found.values()
            ])
            QMetaObject.invokeMethod(
                self, "_on_ble_results",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, data)
            )
        except Exception as e:
            from PyQt6.QtCore import QMetaObject, Q_ARG, Qt
            QMetaObject.invokeMethod(
                self, "_on_ble_error",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, str(e))
            )

    from PyQt6.QtCore import pyqtSlot

    @pyqtSlot(str)
    def _on_ble_results(self, data: str):
        import json
        devices = json.loads(data)
        self._ble_list.clear()

        for d in devices:
            name = d.get("name", "Unknown")
            addr = d.get("address", "")
            rssi = d.get("rssi", "?")
            is_phantom = name.startswith("PL:")

            item = QListWidgetItem()
            icon = "◈" if is_phantom else "○"
            color = MAGENTA if is_phantom else TEXT_SEC
            item.setText(f"{icon}  {name}  [{addr}]  RSSI: {rssi} dBm")
            item.setForeground(QColor(color))
            item.setData(Qt.ItemDataRole.UserRole, addr)
            self._ble_list.addItem(item)

        self._log_msg(f"BLE scan complete: {len(devices)} device(s) found, "
                      f"{sum(1 for d in devices if d.get('name','').startswith('PL:'))} PhantomLink")
        self._stop_scan()

    @pyqtSlot(str)
    def _on_ble_error(self, error: str):
        self._log_msg(f"✗ BLE scan error: {error}")
        self._stop_scan()

    def _stop_scan(self):
        self._scanning = False
        self._scan_btn.setText("⟳  START BLE SCAN")
        self._scan_progress.setVisible(False)

    # ── Classic BT scan ───────────────────────────────────────────────────────

    def _scan_classic(self):
        self._log_msg("Classic BT scan started (8 seconds)…")
        self._paired_list.clear()

        import threading
        def _do_scan():
            from networking.bluetooth_transport import BluetoothManager
            devices = BluetoothManager.get_paired_devices()
            from PyQt6.QtCore import QMetaObject, Q_ARG, Qt
            import json
            QMetaObject.invokeMethod(
                self, "_on_classic_results",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, json.dumps(devices))
            )
        threading.Thread(target=_do_scan, daemon=True).start()

    @pyqtSlot(str)
    def _on_classic_results(self, data: str):
        import json
        devices = json.loads(data)
        self._paired_list.clear()
        for d in devices:
            item = QListWidgetItem(f"○  {d['name']}  [{d['address']}]")
            item.setData(Qt.ItemDataRole.UserRole, d['address'])
            item.setForeground(QColor(TEXT_PRI))
            self._paired_list.addItem(item)
        self._log_msg(f"Classic BT: {len(devices)} device(s) found")

    # ── Connect ───────────────────────────────────────────────────────────────

    def _connect_selected(self):
        item = self._ble_list.currentItem()
        if not item:
            item = self._paired_list.currentItem()
        if not item:
            self._log_msg("✗ Select a device first")
            return

        bt_address = item.data(Qt.ItemDataRole.UserRole)
        # Extract peer_id hint from name if available
        name = item.text()
        peer_id = ""
        if "PL:" in name:
            try:
                peer_id = name.split("PL:")[1].split(" ")[0]
            except Exception:
                pass

        self._log_msg(f"Connecting to {bt_address} via RFCOMM…")
        self.connect_requested.emit(bt_address, peer_id)

    # ── Log helper ────────────────────────────────────────────────────────────

    def _log_msg(self, text: str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.append(f"<span style='color:{TEXT_SEC}'>[{ts}]</span> "
                          f"<span style='color:{TEXT_PRI}'>{text}</span>")

    def add_peer_found(self, info: dict):
        """Called when BT manager discovers a peer — add to list."""
        addr = info.get("ble_address", "?")
        name = info.get("name", "Unknown")
        rssi = info.get("rssi", "?")

        item = QListWidgetItem(f"◈  {name}  [{addr}]  RSSI: {rssi} dBm")
        item.setForeground(QColor(MAGENTA))
        item.setData(Qt.ItemDataRole.UserRole, addr)
        self._ble_list.addItem(item)
        self._log_msg(f"◈ PhantomLink peer found via BLE: {name} @ {addr}")
