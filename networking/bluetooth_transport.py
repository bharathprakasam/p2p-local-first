"""
Bluetooth transport for PhantomLink.
Two layers:
  1. BLE (Bluetooth Low Energy) — discovery/advertising via bleak
     Works on: Windows 10+, macOS 10.13+, Linux (BlueZ 5.43+)
     Range: ~10-30m

  2. Classic Bluetooth RFCOMM — actual data transfer (like a serial pipe)
     Works on: Windows + Linux via PyBluez
     Range: ~10-100m, faster than BLE for data

Flow:
  - BLE: advertise presence + scan for peers (low power, always-on)
  - RFCOMM: once peer found via BLE, open RFCOMM channel for messaging
  - Same JSON length-prefixed protocol as TCP (drop-in compatible)

Install:
  pip install bleak
  pip install PyBluez        # Windows/Linux only
  # macOS: Classic BT needs pyobjc — use BLE-only mode on Mac

Platform notes:
  Windows: Enable Bluetooth in Settings, run as normal user (no admin needed)
  Linux:   sudo usermod -a -G bluetooth $USER  (then re-login)
           sudo systemctl start bluetooth
  macOS:   Bluetooth permission prompt on first run — click Allow
"""

import asyncio
import json
import struct
import logging
import platform
import uuid
from typing import Optional, Callable, Dict, List

logger = logging.getLogger("phantomlink.bluetooth")

# PhantomLink BLE service UUID — unique to our app
# Generated once, hardcoded so all instances recognize each other
PHANTOMLINK_BLE_UUID = "12345678-1234-5678-1234-56789abcdef0"
PHANTOMLINK_RFCOMM_UUID = "12345678-1234-5678-1234-56789abcdef1"
RFCOMM_CHANNEL = 4   # RFCOMM channel number (1-30)

OS = platform.system()


# ─── BLE Discovery (bleak) ────────────────────────────────────────────────────

class BLEDiscovery:
    """
    Uses BLE to advertise this device and discover peers nearby.
    BLE advertising is passive — other devices scan and see us.
    bleak handles scanning; advertising requires platform-specific backend.
    """

    def __init__(self, identity, on_peer_found: Callable):
        self._identity = identity
        self._on_peer_found = on_peer_found
        self._running = False
        self._seen: set = set()   # MAC addresses already reported

    async def start_scanning(self):
        """Scan for BLE devices advertising PhantomLink UUID."""
        try:
            from bleak import BleakScanner
        except ImportError:
            logger.error("bleak not installed: pip install bleak")
            return

        self._running = True
        logger.info("BLE: starting scan for PhantomLink peers…")

        while self._running:
            try:
                # Scan for 5 seconds, return all found devices
                devices = await BleakScanner.discover(
                    timeout=5.0,
                    service_uuids=[PHANTOMLINK_BLE_UUID]
                )
                for device in devices:
                    if device.address in self._seen:
                        continue
                    self._seen.add(device.address)

                    # Device name format: "PL:<device_id_first_8>"
                    name = device.name or ""
                    if name.startswith("PL:"):
                        peer_id_hint = name[3:]
                        logger.info(f"BLE: found PhantomLink peer {name} @ {device.address}")
                        self._on_peer_found({
                            "ble_address": device.address,
                            "name": name,
                            "peer_id_hint": peer_id_hint,
                            "rssi": device.rssi,
                            "transport": "bluetooth_ble",
                        })
            except Exception as e:
                logger.warning(f"BLE scan error: {e}")

            await asyncio.sleep(10)   # scan every 10s

    async def stop(self):
        self._running = False

    async def advertise(self):
        """
        BLE advertising (platform-specific).
        Linux: uses BlueZ D-Bus API
        Windows/macOS: bleak doesn't support advertising yet — use name trick below.

        Workaround for Windows/macOS: set Bluetooth device name to "PL:<device_id[:8]>"
        so scanners can identify us even without service UUID filtering.
        """
        if OS == "Linux":
            await self._advertise_linux()
        else:
            logger.info(
                f"BLE advertising not available on {OS}. "
                f"Ensure your Bluetooth device name is set to: "
                f"PL:{self._identity.device_id[:8]}"
            )

    async def _advertise_linux(self):
        """BlueZ advertisement via D-Bus (Linux only)."""
        try:
            import dbus
            # This requires python-dbus and BlueZ running
            # Simplified: just log instructions
            logger.info(
                "Linux BLE advertising: run this once to set device name:\n"
                f"  bluetoothctl system-alias 'PL:{self._identity.device_id[:8]}'"
            )
        except ImportError:
            logger.warning("dbus not available for BLE advertising")


# ─── Classic Bluetooth RFCOMM ──────────────────────────────────────────────────

class RFCOMMServer:
    """
    Listens for incoming Bluetooth RFCOMM connections.
    Same message protocol as TCP: [4-byte length][JSON payload]
    """

    def __init__(self, identity, db, on_message: Callable, on_connect: Callable):
        self._identity = identity
        self._db = db
        self._on_message = on_message
        self._on_connect = on_connect
        self._running = False
        self._sock = None

    def start(self):
        """Start RFCOMM server in thread (blocking socket API)."""
        if OS == "Darwin":
            logger.info("RFCOMM server: macOS Classic BT not supported via PyBluez. Use BLE-only.")
            return

        import threading
        t = threading.Thread(target=self._serve, daemon=True)
        t.start()

    def _serve(self):
        try:
            import bluetooth  # PyBluez
        except ImportError:
            logger.error("PyBluez not installed: pip install PyBluez")
            return

        try:
            self._sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
            self._sock.bind(("", RFCOMM_CHANNEL))
            self._sock.listen(5)

            # Advertise service so peers can find this channel
            bluetooth.advertise_service(
                self._sock,
                "PhantomLink",
                service_id=PHANTOMLINK_RFCOMM_UUID,
                service_classes=[PHANTOMLINK_RFCOMM_UUID,
                                  bluetooth.SERIAL_PORT_CLASS],
                profiles=[bluetooth.SERIAL_PORT_PROFILE],
            )
            logger.info(f"RFCOMM: listening on channel {RFCOMM_CHANNEL}")
            self._running = True

            while self._running:
                try:
                    client_sock, addr = self._sock.accept()
                    logger.info(f"RFCOMM: incoming from {addr}")
                    import threading
                    t = threading.Thread(
                        target=self._handle_client,
                        args=(client_sock, addr),
                        daemon=True
                    )
                    t.start()
                except Exception as e:
                    if self._running:
                        logger.warning(f"RFCOMM accept error: {e}")

        except Exception as e:
            logger.error(f"RFCOMM server failed: {e}")

    def _handle_client(self, sock, addr):
        """Read length-prefixed JSON messages from RFCOMM client."""
        import threading
        try:
            # First message: connection_request (same protocol as TCP)
            header = self._recv_exact(sock, 4)
            if not header:
                return
            length = struct.unpack(">I", header)[0]
            payload = self._recv_exact(sock, length)
            msg = json.loads(payload.decode())

            if msg.get("type") != "connection_request":
                sock.close()
                return

            peer_id = msg["sender_id"]
            self._on_connect({
                **msg,
                "transport": "bluetooth_rfcomm",
                "bt_address": addr[0],
                "sock": sock,
            })

            # Message loop
            while True:
                header = self._recv_exact(sock, 4)
                if not header:
                    break
                length = struct.unpack(">I", header)[0]
                payload = self._recv_exact(sock, length)
                msg = json.loads(payload.decode())
                self._on_message(peer_id, msg, "bluetooth")

        except Exception as e:
            logger.warning(f"RFCOMM client handler error: {e}")
        finally:
            sock.close()

    def _recv_exact(self, sock, n: int) -> Optional[bytes]:
        """Read exactly n bytes from RFCOMM socket."""
        buf = b""
        while len(buf) < n:
            try:
                chunk = sock.recv(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            except Exception:
                return None
        return buf

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass


class RFCOMMClient:
    """Connect to a peer over Bluetooth RFCOMM and send/receive messages."""

    def __init__(self, identity):
        self._identity = identity
        self._connections: Dict[str, object] = {}  # peer_id → socket

    def connect(self, bt_address: str, peer_id: str) -> bool:
        """
        Connect to peer at bt_address.
        bt_address format: "AA:BB:CC:DD:EE:FF"
        Returns True on success.
        """
        if OS == "Darwin":
            logger.info("RFCOMM client: macOS not supported. Use BLE transport.")
            return False

        try:
            import bluetooth
        except ImportError:
            logger.error("PyBluez not installed: pip install PyBluez")
            return False

        try:
            # Find the RFCOMM channel advertised by peer
            services = bluetooth.find_service(
                uuid=PHANTOMLINK_RFCOMM_UUID,
                address=bt_address
            )
            if not services:
                # Fall back to hardcoded channel
                channel = RFCOMM_CHANNEL
            else:
                channel = services[0]["port"]

            sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
            sock.connect((bt_address, channel))
            logger.info(f"RFCOMM: connected to {bt_address} ch={channel}")

            # Send connection_request (same handshake as TCP)
            import base64
            msg = {
                "type": "connection_request",
                "sender_id": self._identity.device_id,
                "name": self._identity.name,
                "fingerprint": self._identity.fingerprint,
                "public_key": base64.b64encode(
                    self._identity.get_public_key_pem()
                ).decode(),
                "transport": "bluetooth_rfcomm",
            }
            self._send(sock, msg)
            self._connections[peer_id] = sock
            return True

        except Exception as e:
            logger.error(f"RFCOMM connect to {bt_address} failed: {e}")
            return False

    def send_message(self, peer_id: str, msg: dict) -> bool:
        sock = self._connections.get(peer_id)
        if not sock:
            return False
        try:
            self._send(sock, msg)
            return True
        except Exception as e:
            logger.warning(f"RFCOMM send failed: {e}")
            self._connections.pop(peer_id, None)
            return False

    def _send(self, sock, data: dict):
        payload = json.dumps(data).encode()
        sock.send(struct.pack(">I", len(payload)) + payload)

    def disconnect(self, peer_id: str):
        sock = self._connections.pop(peer_id, None)
        if sock:
            try:
                sock.close()
            except Exception:
                pass


# ─── Unified Bluetooth Manager ────────────────────────────────────────────────

class BluetoothManager:
    """
    Single entry point for all Bluetooth functionality.
    Combines BLE discovery + RFCOMM data transfer.

    Usage in node.py:
        self.bt = BluetoothManager(identity, db)
        self.bt.on_peer_found = self._handle_bt_peer
        self.bt.on_message = self._handle_bt_message
        await self.bt.start()
    """

    def __init__(self, identity, db):
        self._identity = identity
        self._db = db

        self.on_peer_found: Optional[Callable] = None
        self.on_message: Optional[Callable] = None
        self.on_connect_request: Optional[Callable] = None

        self._ble = BLEDiscovery(identity, self._on_ble_peer_found)
        self._rfcomm_server = RFCOMMServer(
            identity, db,
            on_message=self._on_rfcomm_message,
            on_connect=self._on_rfcomm_connect,
        )
        self._rfcomm_client = RFCOMMClient(identity)

        # bt_address → peer_id mapping built during discovery
        self._address_to_peer: Dict[str, str] = {}

    async def start(self):
        """Start BLE scanning + RFCOMM server concurrently."""
        logger.info(f"Bluetooth manager starting on {OS}")
        self._rfcomm_server.start()
        # BLE scan runs in async loop
        asyncio.create_task(self._ble.start_scanning())
        asyncio.create_task(self._ble.advertise())

    async def stop(self):
        await self._ble.stop()
        self._rfcomm_server.stop()

    def connect_to_peer(self, bt_address: str, peer_id: str) -> bool:
        """Initiate Bluetooth connection to a discovered peer."""
        success = self._rfcomm_client.connect(bt_address, peer_id)
        if success:
            self._address_to_peer[bt_address] = peer_id
        return success

    def send_message(self, peer_id: str, content: str) -> bool:
        """Send chat message over Bluetooth."""
        import uuid as _uuid
        from datetime import datetime
        from crypto.identity import CryptoEngine
        msg = {
            "type": "chat_message",
            "message_id": str(_uuid.uuid4()),
            "sender_id": self._identity.device_id,
            "timestamp": datetime.utcnow().isoformat(),
            "content": content,
            "hash": CryptoEngine.message_hash(content),
            "transport": "bluetooth",
        }
        return self._rfcomm_client.send_message(peer_id, msg)

    def _on_ble_peer_found(self, info: dict):
        if self.on_peer_found:
            self.on_peer_found(info)

    def _on_rfcomm_message(self, peer_id: str, msg: dict, transport: str):
        if self.on_message:
            self.on_message(peer_id, msg)

    def _on_rfcomm_connect(self, info: dict):
        """Incoming RFCOMM connection — trigger approval flow."""
        if self.on_connect_request:
            self.on_connect_request(info)

    @staticmethod
    def get_paired_devices() -> List[Dict]:
        """Return list of already-paired Bluetooth devices."""
        if OS == "Darwin":
            return []
        try:
            import bluetooth
            nearby = bluetooth.discover_devices(
                duration=8, lookup_names=True, flush_cache=True
            )
            return [{"address": addr, "name": name} for addr, name in nearby]
        except Exception as e:
            logger.warning(f"BT device scan failed: {e}")
            return []

    @staticmethod
    def check_bluetooth_available() -> tuple:
        """Returns (available: bool, reason: str)."""
        try:
            import bleak  # noqa
            ble_ok = True
        except ImportError:
            ble_ok = False

        try:
            import bluetooth  # noqa
            rfcomm_ok = True
        except ImportError:
            rfcomm_ok = False

        if not ble_ok and not rfcomm_ok:
            return False, "Neither bleak nor PyBluez installed"
        if not ble_ok:
            return True, "RFCOMM only (pip install bleak for BLE)"
        if not rfcomm_ok:
            return True, "BLE only — pip install PyBluez for RFCOMM data transfer"
        return True, "Full Bluetooth support (BLE + RFCOMM)"
