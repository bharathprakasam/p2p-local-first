"""networking/node.py"""
import asyncio, json, struct, socket, uuid, base64, logging
from datetime import datetime
from typing import Dict, Optional, Callable

from crypto.identity import DeviceIdentity, CryptoEngine, KeyManager
from database.db_manager import DatabaseManager

logger = logging.getLogger("phantomlink.node")

UDP_PORT               = 47777
TCP_PORT               = 47778
BROADCAST_INTERVAL     = 5.0
GOSSIP_BATCH_THRESHOLD = 50
MAX_MSG_SIZE           = 10 * 1024 * 1024


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close(); return ip
    except Exception: return "127.0.0.1"


def _subnet_broadcast(local_ip: str) -> str:
    parts = local_ip.split(".")
    return f"{parts[0]}.{parts[1]}.{parts[2]}.255" if len(parts) == 4 else "<broadcast>"


class _UDPAnnounceProtocol(asyncio.DatagramProtocol):
    def __init__(self, node: "P2PNode"): self._node = node

    def datagram_received(self, data: bytes, addr):
        try:
            msg = json.loads(data.decode())
            if msg.get("type") == "announce":
                asyncio.ensure_future(self._node._handle_announce(msg, addr[0]))
        except Exception: pass

    def error_received(self, exc): logger.debug(f"UDP error: {exc}")
    def connection_lost(self, exc): logger.debug(f"UDP listener closed: {exc}")


class PeerConnection:
    def __init__(self, peer_id: str, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter):
        self.peer_id = peer_id
        self.reader  = reader
        self.writer  = writer
        self.address = writer.get_extra_info("peername")

    async def send(self, data: dict):
        payload = json.dumps(data).encode("utf-8")
        self.writer.write(struct.pack(">I", len(payload)) + payload)
        await self.writer.drain()

    async def recv(self) -> Optional[dict]:
        header = await self.reader.readexactly(4)
        length = struct.unpack(">I", header)[0]
        if length > MAX_MSG_SIZE: raise ValueError("Message too large")
        return json.loads((await self.reader.readexactly(length)).decode())

    def close(self):
        try: self.writer.close()
        except Exception: pass


class P2PNode:
    def __init__(self, loop, db: DatabaseManager, identity: DeviceIdentity):
        self._loop     = loop
        self._db       = db
        self._identity = identity
        self._key_mgr  = KeyManager(db, identity)
        self._connections: Dict[str, PeerConnection] = {}
        self._pending:     Dict[str, dict]            = {}   # live connection requests only
        self._discovered:  Dict[str, dict]            = {}
        self._running      = False
        self._tcp_server   = None
        self._udp_transport= None
        self._tasks: list  = []
        self._mdns         = None

        self.on_peer_discovered:     Optional[Callable] = None
        self.on_peer_connected:      Optional[Callable] = None
        self.on_peer_disconnected:   Optional[Callable] = None
        self.on_message_received:    Optional[Callable] = None
        self.on_connection_request:  Optional[Callable] = None
        self.on_connection_response: Optional[Callable] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self):
        self._running = True

        # FIX: Reset stale "pending" peers from previous sessions.
        # These have no live connection; approve buttons would silently do nothing.
        for row in self._db.fetchall(
                "SELECT peer_id, name FROM peers WHERE status='pending'"):
            self._db.upsert_peer(row["peer_id"], name=row["name"], status="discovered")
        logger.info("Reset stale pending peers to discovered")

        # TCP server
        self._tcp_server = await asyncio.start_server(
            self._handle_incoming, "0.0.0.0", TCP_PORT)
        logger.info(f"TCP listening on {TCP_PORT}")

        # UDP listener
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try: sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except AttributeError: pass
            sock.bind(("", UDP_PORT))
            self._udp_transport, _ = await self._loop.create_datagram_endpoint(
                lambda: _UDPAnnounceProtocol(self), sock=sock)
            logger.info(f"UDP listener ready on port {UDP_PORT}")
        except Exception as e:
            logger.error(f"UDP bind failed on {UDP_PORT}: {e}\n"
                         "  → Windows: allow port 47777 in Windows Firewall\n"
                         "  → Linux:   sudo ufw allow 47777/udp")

        # mDNS (zeroconf fallback)
        await self._start_mdns()

        self._tasks = [
            asyncio.create_task(self._udp_broadcaster(), name="udp_broadcast"),
            asyncio.create_task(self._key_expiry_checker(), name="key_expiry"),
        ]
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self):
        self._running = False
        for t in self._tasks: t.cancel()
        if self._udp_transport: self._udp_transport.close()
        if self._tcp_server:
            self._tcp_server.close(); await self._tcp_server.wait_closed()
        if self._mdns:
            try: await self._mdns.stop()
            except Exception: pass
        for c in list(self._connections.values()): c.close()

    # ── mDNS ───────────────────────────────────────────────────────────────────

    async def _start_mdns(self):
        try:
            from networking.mdns_discovery import MDNSDiscovery
            self._mdns = MDNSDiscovery(self._identity, tcp_port=TCP_PORT)
            self._mdns.on_peer_found = self._handle_mdns_peer_found
            self._mdns.on_peer_lost  = self._handle_mdns_peer_lost
            await self._mdns.start()
            logger.info("mDNS discovery: active")
        except ImportError:
            logger.info("mDNS: pip install zeroconf — using UDP only")
        except Exception as e:
            logger.warning(f"mDNS start failed: {e}")

    def _handle_mdns_peer_found(self, peer_info: dict):
        peer_id = peer_info.get("peer_id", "")
        if not peer_id or peer_id == self._identity.device_id: return
        is_new = peer_id not in self._discovered
        info = {
            "peer_id":     peer_id,
            "name":        peer_info.get("name", "Unknown"),
            "ip":          peer_info.get("ip", ""),
            "port":        peer_info.get("port", TCP_PORT),
            "fingerprint": peer_info.get("fingerprint", ""),
            "last_seen":   datetime.utcnow().isoformat(),
            "transport":   "mdns",
        }
        self._discovered[peer_id] = info
        self._db.upsert_peer(peer_id, name=info["name"],
                             last_ip=info["ip"], last_port=info["port"])
        if self.on_peer_discovered: self.on_peer_discovered(info, is_new)
        peer_row = self._db.get_peer(peer_id)
        if (peer_row and peer_row["status"] == "trusted"
                and peer_id not in self._connections):
            asyncio.create_task(
                self._auto_reconnect(peer_id, info["ip"], info["port"]))

    def _handle_mdns_peer_lost(self, peer_id: str):
        self._discovered.pop(peer_id, None)

    # ── UDP ────────────────────────────────────────────────────────────────────

    async def _udp_broadcaster(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        msg = json.dumps({
            "type": "announce", "tcp_port": TCP_PORT,
            **self._identity.to_announce_dict()
        }).encode()
        while self._running:
            local_ip     = _get_local_ip()
            subnet_bcast = _subnet_broadcast(local_ip)
            targets      = list({"255.255.255.255", subnet_bcast})
            for t in targets:
                try:
                    await self._loop.run_in_executor(
                        None, lambda _t=t: sock.sendto(msg, (_t, UDP_PORT)))
                except Exception as e:
                    logger.debug(f"UDP broadcast to {t} failed: {e}")
            await asyncio.sleep(BROADCAST_INTERVAL)
        sock.close()

    async def _handle_announce(self, msg: dict, source_ip: str):
        peer_id = msg.get("device_id")
        if not peer_id or peer_id == self._identity.device_id: return
        info = {
            "peer_id":     peer_id,
            "name":        msg.get("name", "Unknown"),
            "ip":          source_ip,
            "port":        msg.get("tcp_port", TCP_PORT),
            "fingerprint": msg.get("fingerprint", ""),
            "public_key":  msg.get("public_key", ""),
            "last_seen":   datetime.utcnow().isoformat(),
            "transport":   "udp",
        }
        is_new = peer_id not in self._discovered
        self._discovered[peer_id] = info
        self._db.upsert_peer(peer_id, name=info["name"],
                             last_ip=source_ip, last_port=info["port"])
        if self.on_peer_discovered: self.on_peer_discovered(info, is_new)
        peer_row = self._db.get_peer(peer_id)
        if (peer_row and peer_row["status"] == "trusted"
                and peer_id not in self._connections):
            asyncio.create_task(
                self._auto_reconnect(peer_id, source_ip, info["port"]))

    async def _auto_reconnect(self, peer_id, ip, port):
        await asyncio.sleep(1)
        if peer_id not in self._connections:
            try: await self.connect_to_peer(ip, port)
            except Exception: pass

    # ── TCP server ─────────────────────────────────────────────────────────────

    async def _handle_incoming(self, reader, writer):
        addr = writer.get_extra_info("peername")
        try:
            header  = await asyncio.wait_for(reader.readexactly(4), timeout=10)
            length  = struct.unpack(">I", header)[0]
            payload = await asyncio.wait_for(reader.readexactly(length), timeout=10)
            msg     = json.loads(payload.decode())
            if msg.get("type") != "connection_request":
                writer.close(); return
            peer_id       = msg["sender_id"]
            pub_key_bytes = base64.b64decode(msg["public_key"]) if msg.get("public_key") else None

            # Store live connection in _pending
            self._pending[peer_id] = {
                **msg, "reader": reader, "writer": writer, "addr": addr
            }
            self._db.upsert_peer(peer_id, name=msg.get("name", ""),
                                 last_ip=addr[0], last_port=addr[1],
                                 public_key=pub_key_bytes,
                                 fingerprint=msg.get("fingerprint", ""),
                                 status="pending")
            self._db.log_event("connection_request", peer_id,
                               json.dumps({"ip": addr[0]}))

            # Fire callback — only pass serializable fields, NOT reader/writer
            if self.on_connection_request:
                self.on_connection_request({
                    "sender_id":   peer_id,
                    "peer_id":     peer_id,
                    "name":        msg.get("name", "Unknown"),
                    "fingerprint": msg.get("fingerprint", ""),
                    "addr":        list(addr),
                    "public_key":  msg.get("public_key", ""),
                })
        except Exception as e:
            logger.warning(f"Handshake failed from {addr}: {e}")
            try: writer.close()
            except Exception: pass

    # ── Connect / approve / reject ─────────────────────────────────────────────

    async def connect_to_peer(self, ip: str, port: int):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=10)
        except Exception as e:
            logger.warning(f"TCP connect to {ip}:{port} failed: {e}"); return
        conn = PeerConnection("__pending__", reader, writer)
        await conn.send({
            "type":        "connection_request",
            "sender_id":   self._identity.device_id,
            "name":        self._identity.name,
            "fingerprint": self._identity.fingerprint,
            "public_key":  base64.b64encode(
                               self._identity.get_public_key_pem()).decode()
        })
        try:
            response = await asyncio.wait_for(conn.recv(), timeout=60)
        except asyncio.TimeoutError:
            logger.warning(f"No response from {ip}:{port} in 60s")
            conn.close(); return
        if response.get("type") != "connection_response":
            conn.close(); return
        if not response.get("accepted"):
            conn.close()
            if self.on_connection_response:
                self.on_connection_response(response.get("sender_id"), False)
            return
        peer_id = response["sender_id"]
        conn.peer_id = peer_id
        if "encrypted_key" in response:
            self._key_mgr.receive_shared_key(
                peer_id, base64.b64decode(response["encrypted_key"]))
        if "public_key" in response:
            pub_bytes = base64.b64decode(response["public_key"])
            self._db.upsert_peer(peer_id, name=response.get("name", ""),
                                 status="trusted", public_key=pub_bytes,
                                 fingerprint=response.get("fingerprint", ""))
        self._connections[peer_id] = conn
        self._db.log_event("connection_established", peer_id,
                           json.dumps({"ip": ip}))
        if self.on_peer_connected: self.on_peer_connected(peer_id)
        asyncio.create_task(self._message_loop(conn))
        asyncio.create_task(self._initiate_gossip(peer_id))

    async def approve_connection(self, peer_id: str):
        pending = self._pending.pop(peer_id, None)
        if not pending:
            logger.warning(f"approve_connection: no live pending for {peer_id[:8]}")
            return
        reader, writer = pending["reader"], pending["writer"]
        conn          = PeerConnection(peer_id, reader, writer)
        pub_key_bytes = base64.b64decode(pending["public_key"]) if pending.get("public_key") else None
        self._db.upsert_peer(peer_id, name=pending.get("name", ""),
                             status="trusted", public_key=pub_key_bytes,
                             fingerprint=pending.get("fingerprint", ""))
        enc_for_peer = None
        if pub_key_bytes:
            enc_for_peer = self._key_mgr.establish_key_for_peer(peer_id, pub_key_bytes)
        response = {
            "type":        "connection_response",
            "accepted":    True,
            "sender_id":   self._identity.device_id,
            "name":        self._identity.name,
            "fingerprint": self._identity.fingerprint,
            "public_key":  base64.b64encode(
                               self._identity.get_public_key_pem()).decode()
        }
        if enc_for_peer:
            response["encrypted_key"] = base64.b64encode(enc_for_peer).decode()
        try:
            await conn.send(response)
        except Exception as e:
            logger.error(f"approve_connection send failed: {e}"); return
        self._connections[peer_id] = conn
        self._db.log_event("connection_approved", peer_id)
        if self.on_peer_connected: self.on_peer_connected(peer_id)
        asyncio.create_task(self._message_loop(conn))
        asyncio.create_task(self._initiate_gossip(peer_id))

    async def reject_connection(self, peer_id: str):
        pending = self._pending.pop(peer_id, None)
        if not pending: return
        conn = PeerConnection(peer_id, pending["reader"], pending["writer"])
        try:
            await conn.send({"type": "connection_response", "accepted": False,
                             "sender_id": self._identity.device_id})
        except Exception: pass
        conn.close()
        self._db.upsert_peer(peer_id, name=pending.get("name", ""), status="blocked")
        self._db.log_event("connection_rejected", peer_id)

    # ── Message loop ───────────────────────────────────────────────────────────

    async def _message_loop(self, conn: PeerConnection):
        try:
            while self._running and conn.peer_id in self._connections:
                msg = await asyncio.wait_for(conn.recv(), timeout=120)
                await self._dispatch(conn, msg)
        except asyncio.TimeoutError:
            try: await conn.send({"type": "ping",
                                  "sender_id": self._identity.device_id})
            except Exception: pass
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError): pass
        except Exception as e:
            logger.warning(f"Message loop {conn.peer_id[:8]}: {e}")
        finally:
            self._on_disconnect(conn.peer_id)

    def _on_disconnect(self, peer_id: str):
        self._connections.pop(peer_id, None)
        self._db.log_event("disconnected", peer_id)
        if self.on_peer_disconnected: self.on_peer_disconnected(peer_id)

    async def _dispatch(self, conn: PeerConnection, msg: dict):
        t = msg.get("type")
        if   t == "chat_message":    await self._recv_chat(conn, msg)
        elif t == "gossip_meta":     await self._handle_gossip_meta(conn, msg)
        elif t == "gossip_request":  await self._handle_gossip_request(conn, msg)
        elif t == "gossip_response": await self._handle_gossip_response(conn, msg)
        elif t == "ping":
            await conn.send({"type": "pong",
                             "sender_id": self._identity.device_id})
        elif self.on_message_received:
            self.on_message_received(msg)

    # ── Chat ───────────────────────────────────────────────────────────────────

    async def send_message(self, peer_id: str, content: str) -> Optional[str]:
        conn = self._connections.get(peer_id)
        if not conn: return None
        mid          = str(uuid.uuid4())
        timestamp    = datetime.utcnow().isoformat()
        content_hash = CryptoEngine.message_hash(content)
        sign_data    = f"{mid}|{self._identity.device_id}|{timestamp}|{content_hash}".encode()
        signature    = CryptoEngine.rsa_sign(
            self._identity.get_private_key_pem(), sign_data)
        key = self._key_mgr.get_or_create_shared_key(peer_id)
        if key: enc, nonce = CryptoEngine.aes_encrypt(key, content.encode())
        else:   enc, nonce = content.encode(), b"\x00" * 12
        self._db.save_message(mid, self._identity.device_id, peer_id,
                              timestamp, enc, content_hash, nonce, "sent")
        await conn.send({
            "type": "chat_message", "message_id": mid,
            "sender_id": self._identity.device_id, "timestamp": timestamp,
            "content": content, "hash": content_hash,
            "signature": base64.b64encode(signature).decode()
        })
        self._db.mark_synced(mid, peer_id)
        return mid

    async def _recv_chat(self, conn: PeerConnection, msg: dict):
        mid      = msg.get("message_id", str(uuid.uuid4()))
        sender   = msg.get("sender_id", "")
        ts       = msg.get("timestamp", datetime.utcnow().isoformat())
        content  = msg.get("content", "")
        rec_hash = msg.get("hash", "")
        sig_b64  = msg.get("signature", "")
        verified = CryptoEngine.verify_hash(content, rec_hash)
        signed   = False
        if sig_b64:
            peer_row = self._db.get_peer(conn.peer_id)
            if peer_row and peer_row["public_key"]:
                sign_data = f"{mid}|{sender}|{ts}|{rec_hash}".encode()
                signed = CryptoEngine.rsa_verify(
                    peer_row["public_key"], sign_data, base64.b64decode(sig_b64))
        key = self._key_mgr.get_or_create_shared_key(conn.peer_id)
        if key: enc, nonce = CryptoEngine.aes_encrypt(key, content.encode())
        else:   enc, nonce = content.encode(), b"\x00" * 12
        self._db.save_message(mid, sender, conn.peer_id, ts, enc,
                              CryptoEngine.message_hash(content), nonce, "received")
        self._db.mark_synced(mid, conn.peer_id)
        try:
            await conn.send({
                "type": "read_receipt", "message_id": mid,
                "status": "delivered", "sender_id": self._identity.device_id,
                "timestamp": datetime.utcnow().isoformat()
            })
        except Exception: pass
        if self.on_message_received:
            self.on_message_received({
                "message_id": mid, "sender_id": sender,
                "peer_id": conn.peer_id, "timestamp": ts,
                "content": content, "hash": rec_hash,
                "verified": verified, "signed": signed
            })

    # ── Gossip ─────────────────────────────────────────────────────────────────

    async def _initiate_gossip(self, peer_id: str):
        conn = self._connections.get(peer_id)
        if not conn: return
        meta = self._db.get_message_ids_for_peer(peer_id)
        if len(meta) > GOSSIP_BATCH_THRESHOLD:
            meta = sorted(meta, key=lambda m: m["timestamp"])[-50:]
        await conn.send({"type": "gossip_meta",
                         "sender_id": self._identity.device_id, "messages": meta})

    async def _handle_gossip_meta(self, conn: PeerConnection, msg: dict):
        their_ids = {m["message_id"] for m in msg.get("messages", [])}
        our_ids   = {r["message_id"]
                     for r in self._db.get_message_ids_for_peer(conn.peer_id)}
        missing   = list(their_ids - our_ids)
        if missing:
            await conn.send({"type": "gossip_request",
                             "sender_id": self._identity.device_id,
                             "message_ids": missing})
        await self._initiate_gossip(conn.peer_id)

    async def _handle_gossip_request(self, conn: PeerConnection, msg: dict):
        full = []
        for mid in msg.get("message_ids", []):
            row = self._db.get_message_by_id(mid)
            if not row: continue
            key = self._key_mgr.get_or_create_shared_key(conn.peer_id)
            try:
                text = (CryptoEngine.aes_decrypt(key, row["content"],
                                                  row["nonce"]).decode()
                        if key else row["content"].decode(errors="replace"))
            except Exception: text = "[error]"
            full.append({"message_id": row["message_id"],
                         "sender_id": row["sender_id"],
                         "peer_id": row["peer_id"],
                         "timestamp": row["timestamp"],
                         "content": text, "hash": row["content_hash"]})
        await conn.send({"type": "gossip_response",
                         "sender_id": self._identity.device_id, "messages": full})

    async def _handle_gossip_response(self, conn: PeerConnection, msg: dict):
        stored = 0
        for m in msg.get("messages", []):
            if self._db.get_message_by_id(m["message_id"]): continue
            content = m.get("content", "")
            key = self._key_mgr.get_or_create_shared_key(conn.peer_id)
            if key: enc, nonce = CryptoEngine.aes_encrypt(key, content.encode())
            else:   enc, nonce = content.encode(), b"\x00" * 12
            direction = ("received" if m["sender_id"] != self._identity.device_id
                         else "sent")
            self._db.save_message(m["message_id"], m["sender_id"], conn.peer_id,
                                  m["timestamp"], enc, m["hash"], nonce,
                                  direction, synced=1)
            stored += 1
        if stored:
            if self.on_message_received:
                self.on_message_received({"type": "sync_complete",
                                          "peer_id": conn.peer_id, "count": stored})

    # ── Key expiry ─────────────────────────────────────────────────────────────

    async def _key_expiry_checker(self):
        while self._running:
            for row in self._db.get_expired_keys():
                self._db.delete_peer_key(row["peer_id"])
                self._db.log_event("key_deleted", row["peer_id"],
                                   '{"reason":"expiry"}')
            await asyncio.sleep(60)

    # ── Public helpers ─────────────────────────────────────────────────────────

    def get_discovered_peers(self): return list(self._discovered.values())
    def get_connected_peers(self):  return list(self._connections.keys())
    def get_pending_peer_ids(self): return set(self._pending.keys())
    def is_connected(self, peer_id): return peer_id in self._connections

    def decrypt_message_content(self, peer_id, content, nonce):
        key = self._key_mgr.get_or_create_shared_key(peer_id)
        if not key: return None
        try: return CryptoEngine.aes_decrypt(key, content, nonce).decode("utf-8")
        except Exception: return None
