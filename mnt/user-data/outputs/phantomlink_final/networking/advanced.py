"""
networking/advanced.py — Advanced networking for PhantomLink v2

1. NAT Traversal (UDP Hole Punching)
   - Peers behind NAT exchange external IP:port via a rendezvous signal
   - Both send UDP simultaneously to punch holes in each NAT
   - Once hole punched, establish TCP connection through it

2. Store-and-Forward Relay
   - Trusted peer stores messages for offline recipient
   - On reconnect: recipient requests stored messages → delivered

3. Multi-hop Routing
   - Route messages A→B→C when A↔C not directly connected
   - Uses Dijkstra on known peer graph
   - Each hop forwards using existing TCP connections
"""

import asyncio
import json
import socket
import struct
import time
import uuid
import logging
from typing import Dict, List, Optional, Tuple, Set
from datetime import datetime

logger = logging.getLogger("phantomlink.advanced_net")

STUN_SERVERS = [
    ("stun.l.google.com", 19302),
    ("stun1.l.google.com", 19302),
    ("stun.cloudflare.com", 3478),
]

# ══════════════════════════════════════════════════════════════════════════════
# 1. NAT TRAVERSAL — UDP Hole Punching
# ══════════════════════════════════════════════════════════════════════════════

class STUNClient:
    """
    Discovers external (public) IP:port using STUN protocol.
    RFC 5389 — sends Binding Request, parses XOR-MAPPED-ADDRESS response.
    """

    MAGIC_COOKIE = 0x2112A442
    BINDING_REQUEST = 0x0001
    BINDING_RESPONSE = 0x0101
    XOR_MAPPED_ADDRESS = 0x0020
    MAPPED_ADDRESS = 0x0001

    @classmethod
    async def get_external_address(cls, loop=None) -> Optional[Tuple[str, int]]:
        """Try multiple STUN servers. Returns (external_ip, external_port) or None."""
        for host, port in STUN_SERVERS:
            try:
                result = await asyncio.wait_for(
                    cls._query_stun(host, port), timeout=5.0
                )
                if result:
                    logger.info(f"STUN: external address = {result[0]}:{result[1]}")
                    return result
            except Exception as e:
                logger.debug(f"STUN {host}:{port} failed: {e}")
        return None

    @classmethod
    async def _query_stun(cls, host: str, port: int) -> Optional[Tuple[str, int]]:
        loop = asyncio.get_event_loop()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind(("", 0))  # bind to any available port

        # Build STUN Binding Request
        transaction_id = os.urandom(12)
        msg = struct.pack(">HHI", cls.BINDING_REQUEST, 0, cls.MAGIC_COOKIE)
        msg += transaction_id

        await loop.sock_sendto(sock, msg, (host, port))

        # Wait for response
        data, _ = await loop.sock_recvfrom(sock, 1024)
        sock.close()

        return cls._parse_stun_response(data)

    @classmethod
    def _parse_stun_response(cls, data: bytes) -> Optional[Tuple[str, int]]:
        if len(data) < 20:
            return None
        msg_type, msg_len, magic = struct.unpack(">HHI", data[:8])
        if msg_type != cls.BINDING_RESPONSE:
            return None

        offset = 20  # skip 20-byte header
        while offset < len(data) - 4:
            attr_type, attr_len = struct.unpack(">HH", data[offset:offset+4])
            offset += 4
            attr_val = data[offset:offset+attr_len]
            offset += attr_len + (4 - attr_len % 4) % 4  # 4-byte align

            if attr_type == cls.XOR_MAPPED_ADDRESS:
                # XOR-MAPPED-ADDRESS: family(1) + port(2) + addr(4)
                family = attr_val[1]
                if family == 0x01:  # IPv4
                    port = struct.unpack(">H", attr_val[2:4])[0]
                    port ^= (cls.MAGIC_COOKIE >> 16)
                    ip_int = struct.unpack(">I", attr_val[4:8])[0]
                    ip_int ^= cls.MAGIC_COOKIE
                    ip = socket.inet_ntoa(struct.pack(">I", ip_int))
                    return ip, port

            elif attr_type == cls.MAPPED_ADDRESS:
                family = attr_val[1]
                if family == 0x01:
                    port = struct.unpack(">H", attr_val[2:4])[0]
                    ip = socket.inet_ntoa(attr_val[4:8])
                    return ip, port

        return None


import os  # needed for os.urandom above


class NATTraversal:
    """
    UDP Hole Punching coordinator.

    Protocol:
      1. Both peers discover external IP:port via STUN
      2. Exchange external addresses via existing connection (or out-of-band)
      3. Both simultaneously send UDP packets to each other's external address
         (this punches hole in both NATs)
      4. Once UDP hole punched → establish TCP through same port mapping
         (works for cone NATs; symmetric NAT requires relay fallback)

    Signaling: peers exchange STUN addresses via the rendezvous channel
    (any existing connection, e.g. LAN peer that both are connected to).
    """

    def __init__(self, loop):
        self._loop = loop
        self._external_addr: Optional[Tuple[str, int]] = None
        # peer_id → their external address
        self._peer_externals: Dict[str, Tuple[str, int]] = {}

    async def get_my_external_addr(self) -> Optional[Tuple[str, int]]:
        if not self._external_addr:
            self._external_addr = await STUNClient.get_external_address()
        return self._external_addr

    async def punch_hole(self, peer_external_ip: str, peer_external_port: int,
                          local_port: int = 0) -> bool:
        """
        Send UDP packets to peer's external address to punch NAT hole.
        Both sides do this simultaneously (coordinated via signal).
        Returns True if we get a response (hole punched successfully).
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if local_port:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", local_port))
        sock.setblocking(False)

        # Send 10 punch packets over 3 seconds
        punch_msg = b"PHLK_PUNCH"
        punched = False

        for attempt in range(10):
            try:
                await self._loop.sock_sendto(
                    sock, punch_msg, (peer_external_ip, peer_external_port)
                )
                # Try to receive response
                try:
                    data, addr = await asyncio.wait_for(
                        self._loop.sock_recvfrom(sock, 64), timeout=0.3
                    )
                    if data == b"PHLK_PUNCH_ACK":
                        punched = True
                        break
                    elif data == b"PHLK_PUNCH":
                        # Send ack
                        await self._loop.sock_sendto(
                            sock, b"PHLK_PUNCH_ACK", addr
                        )
                        punched = True
                        break
                except asyncio.TimeoutError:
                    pass
            except Exception as e:
                logger.debug(f"Punch attempt {attempt} failed: {e}")

            await asyncio.sleep(0.3)

        sock.close()
        logger.info(f"NAT punch to {peer_external_ip}:{peer_external_port} "
                    f"{'succeeded' if punched else 'failed'}")
        return punched

    async def attempt_connection(self, peer_id: str,
                                  peer_external: Tuple[str, int],
                                  node) -> bool:
        """
        Full NAT traversal attempt:
        1. Punch UDP hole
        2. Try TCP connect through punched port
        """
        ip, port = peer_external
        logger.info(f"NAT traversal: attempting {ip}:{port} for {peer_id[:8]}")

        punched = await self.punch_hole(ip, port)
        if punched:
            try:
                # Try TCP connect to same IP:port
                await asyncio.wait_for(
                    node.connect_to_peer(ip, port), timeout=10.0
                )
                return True
            except Exception as e:
                logger.warning(f"TCP after punch failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 2. STORE-AND-FORWARD RELAY
# ══════════════════════════════════════════════════════════════════════════════

class RelayStore:
    """
    In-memory + SQLite store for messages waiting for offline peers.
    A trusted peer acts as relay: stores messages, delivers on reconnect.

    Schema addition (handled in db_manager):
      relay_queue (relay_id, target_peer_id, sender_id, payload_json,
                   stored_at, delivered, ttl_hours)
    """

    def __init__(self, db, identity):
        self._db = db
        self._identity = identity
        self._ensure_table()

    def _ensure_table(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS relay_queue (
                relay_id      TEXT PRIMARY KEY,
                target_peer_id TEXT NOT NULL,
                sender_id     TEXT NOT NULL,
                payload       TEXT NOT NULL,
                stored_at     TEXT NOT NULL,
                delivered     INTEGER DEFAULT 0,
                ttl_hours     INTEGER DEFAULT 72
            )
        """)
        self._db.commit()

    def store(self, target_peer_id: str, sender_id: str,
              payload: dict, ttl_hours: int = 72) -> str:
        """Store message for offline peer. Returns relay_id."""
        relay_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        self._db.execute("""
            INSERT INTO relay_queue
            (relay_id, target_peer_id, sender_id, payload, stored_at, ttl_hours)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (relay_id, target_peer_id, sender_id,
              json.dumps(payload), now, ttl_hours))
        self._db.commit()
        logger.info(f"Relay: stored msg {relay_id[:8]} for {target_peer_id[:8]}")
        return relay_id

    def get_pending(self, target_peer_id: str) -> List[dict]:
        """Get all undelivered messages for a peer."""
        rows = self._db.fetchall("""
            SELECT * FROM relay_queue
            WHERE target_peer_id=? AND delivered=0
            ORDER BY stored_at ASC
        """, (target_peer_id,))
        return [dict(r) for r in rows]

    def mark_delivered(self, relay_id: str):
        self._db.execute(
            "UPDATE relay_queue SET delivered=1 WHERE relay_id=?",
            (relay_id,)
        )
        self._db.commit()

    def purge_expired(self):
        """Remove delivered + expired messages."""
        self._db.execute("""
            DELETE FROM relay_queue
            WHERE delivered=1
            OR datetime(stored_at, '+' || ttl_hours || ' hours') < datetime('now')
        """)
        self._db.commit()

    def get_relay_count(self) -> int:
        row = self._db.fetchone(
            "SELECT COUNT(*) as c FROM relay_queue WHERE delivered=0"
        )
        return row["c"] if row else 0


class RelayManager:
    """
    Manages relay behavior:
    - As sender: if target offline, find trusted relay peer, send via them
    - As relay: accept relay_store requests, deliver when target connects
    - As recipient: on connect, request pending relay messages
    """

    def __init__(self, db, identity, node):
        self._db = db
        self._identity = identity
        self._node = node
        self._store = RelayStore(db, identity)

    async def send_via_relay(self, target_peer_id: str,
                              message: dict,
                              relay_peer_id: str) -> bool:
        """
        Send message to target via relay peer.
        relay_peer must be trusted + connected to us + (hopefully) to target.
        """
        conn = self._node._connections.get(relay_peer_id)
        if not conn:
            return False

        await conn.send({
            "type": "relay_store",
            "relay_id": str(uuid.uuid4()),
            "target_peer_id": target_peer_id,
            "sender_id": self._identity.device_id,
            "payload": message,
            "ttl_hours": 72,
        })
        logger.info(f"Relay: sent via {relay_peer_id[:8]} → {target_peer_id[:8]}")
        return True

    async def handle_relay_store(self, conn, msg: dict):
        """We are acting as relay. Store message for target."""
        target = msg.get("target_peer_id")
        sender = msg.get("sender_id")
        payload = msg.get("payload", {})
        relay_id = msg.get("relay_id", str(uuid.uuid4()))
        ttl = msg.get("ttl_hours", 72)

        self._store.store(target, sender, payload, ttl)
        self._db.log_event("relay_stored", sender,
                           json.dumps({"target": target, "relay_id": relay_id[:8]}))

        # Ack to sender
        try:
            await conn.send({
                "type": "relay_ack",
                "relay_id": relay_id,
                "stored": True,
            })
        except Exception:
            pass

    async def deliver_pending(self, target_peer_id: str):
        """Target peer just connected. Deliver all stored messages."""
        pending = self._store.get_pending(target_peer_id)
        if not pending:
            return

        conn = self._node._connections.get(target_peer_id)
        if not conn:
            return

        logger.info(f"Relay: delivering {len(pending)} stored messages to {target_peer_id[:8]}")
        for item in pending:
            try:
                payload = json.loads(item["payload"])
                payload["type"] = payload.get("type", "chat_message")
                payload["relayed"] = True
                payload["relay_stored_at"] = item["stored_at"]
                await conn.send(payload)
                self._store.mark_delivered(item["relay_id"])
                await asyncio.sleep(0.05)  # small delay between deliveries
            except Exception as e:
                logger.warning(f"Relay delivery failed: {e}")

        self._db.log_event("relay_delivered", target_peer_id,
                           json.dumps({"count": len(pending)}))

    async def request_relay_messages(self, relay_peer_id: str):
        """Ask a relay peer if they have stored messages for us."""
        conn = self._node._connections.get(relay_peer_id)
        if not conn:
            return
        await conn.send({
            "type": "relay_request",
            "target_peer_id": self._identity.device_id,
        })


# ══════════════════════════════════════════════════════════════════════════════
# 3. MULTI-HOP ROUTING
# ══════════════════════════════════════════════════════════════════════════════

class PeerGraph:
    """
    Tracks known peer topology for routing decisions.
    Each peer broadcasts their connected peers list periodically.
    We build a graph and run Dijkstra to find routes.
    """

    def __init__(self):
        # peer_id → set of peer_ids they're connected to
        self._adjacency: Dict[str, Set[str]] = {}

    def update_neighbors(self, peer_id: str, neighbors: List[str]):
        self._adjacency[peer_id] = set(neighbors)

    def add_edge(self, a: str, b: str):
        self._adjacency.setdefault(a, set()).add(b)
        self._adjacency.setdefault(b, set()).add(a)

    def find_route(self, source: str, target: str) -> Optional[List[str]]:
        """
        BFS shortest path from source to target.
        Returns list of peer_ids [source, hop1, hop2, ..., target]
        or None if no path exists.
        """
        if source == target:
            return [source]
        if target in self._adjacency.get(source, set()):
            return [source, target]

        visited = {source}
        queue = [[source]]

        while queue:
            path = queue.pop(0)
            current = path[-1]

            for neighbor in self._adjacency.get(current, set()):
                if neighbor in visited:
                    continue
                new_path = path + [neighbor]
                if neighbor == target:
                    return new_path
                visited.add(neighbor)
                queue.append(new_path)

        return None  # no route found


class MultiHopRouter:
    """
    Routes messages through intermediate connected peers.

    Protocol message types:
      route_message: {type, route: [peer_ids], hop_index, payload, msg_id}
      Each hop increments hop_index and forwards to route[hop_index+1]
    """

    def __init__(self, identity, node, graph: PeerGraph):
        self._identity = identity
        self._node = node
        self._graph = graph
        self._seen_msgs: Set[str] = set()  # prevent routing loops

    async def send_via_route(self, target_peer_id: str,
                              payload: dict) -> bool:
        """
        Find route to target and send message through it.
        Returns False if no route found.
        """
        my_id = self._identity.device_id

        # First check direct connection
        if self._node.is_connected(target_peer_id):
            conn = self._node._connections.get(target_peer_id)
            if conn:
                await conn.send(payload)
                return True

        # Find multi-hop route
        route = self._graph.find_route(my_id, target_peer_id)
        if not route or len(route) < 2:
            logger.warning(f"No route to {target_peer_id[:8]}")
            return False

        # Send to first hop
        first_hop = route[1]
        conn = self._node._connections.get(first_hop)
        if not conn:
            return False

        msg_id = str(uuid.uuid4())
        await conn.send({
            "type": "route_message",
            "msg_id": msg_id,
            "route": route,
            "hop_index": 0,
            "origin": my_id,
            "payload": payload,
            "sent_at": datetime.utcnow().isoformat(),
        })
        logger.info(f"Multi-hop: sent via {' → '.join(r[:8] for r in route)}")
        return True

    async def handle_route_message(self, conn, msg: dict):
        """
        We received a routed message. Either:
        - We are the destination → process payload
        - We are intermediate hop → forward to next hop
        """
        msg_id = msg.get("msg_id", "")
        if msg_id in self._seen_msgs:
            return  # loop prevention
        self._seen_msgs.add(msg_id)

        # Limit seen cache size
        if len(self._seen_msgs) > 10000:
            self._seen_msgs = set(list(self._seen_msgs)[-5000:])

        route = msg.get("route", [])
        hop_index = msg.get("hop_index", 0)
        my_id = self._identity.device_id

        # Find our position in route
        try:
            my_pos = route.index(my_id)
        except ValueError:
            logger.warning("Route message: we're not in route, dropping")
            return

        next_pos = my_pos + 1
        if next_pos >= len(route):
            # We are destination — deliver payload
            payload = msg.get("payload", {})
            logger.info(f"Multi-hop: received final delivery from {msg.get('origin', '?')[:8]}")
            await self._node._dispatch(conn, payload)
        else:
            # Forward to next hop
            next_peer_id = route[next_pos]
            next_conn = self._node._connections.get(next_peer_id)
            if next_conn:
                msg["hop_index"] = next_pos
                await next_conn.send(msg)
                logger.info(f"Multi-hop: forwarded to {next_peer_id[:8]}")
            else:
                logger.warning(f"Multi-hop: next hop {next_peer_id[:8]} not connected")
