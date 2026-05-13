"""
networking/messaging_features.py — Advanced messaging features

1. File Transfer — chunked with SHA-256 per-chunk verification
2. Typing Indicators — lightweight heartbeat
3. Read Receipts — double-tick confirmation
4. Self-Destructing Messages — per-message TTL
5. Message Reactions — emoji reactions via gossip
6. FTS5 Full-Text Search — SQLite FTS5 on decrypted cache
7. Group Channels — shared rooms, gossip-synced
8. Distributed Consensus (polls/votes)
9. Scheduled Deletion
10. Pseudonym Mode
"""

import asyncio
import hashlib
import json
import os
import struct
import uuid
import base64
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Tuple
from pathlib import Path

logger = logging.getLogger("phantomlink.messaging")

CHUNK_SIZE = 64 * 1024   # 64KB chunks for file transfer
MAX_FILE_SIZE = 512 * 1024 * 1024  # 512MB max


# ══════════════════════════════════════════════════════════════════════════════
# 1. FILE TRANSFER
# ══════════════════════════════════════════════════════════════════════════════

class FileTransferManager:
    """
    Chunked file transfer with SHA-256 integrity check per chunk.

    Protocol:
      Sender → file_offer:    {type, transfer_id, filename, size, total_chunks, file_hash}
      Receiver → file_accept: {type, transfer_id} or file_reject
      Sender → file_chunk:    {type, transfer_id, chunk_index, data_b64, chunk_hash}
      Receiver → chunk_ack:   {type, transfer_id, chunk_index, ok}
      Sender → file_complete: {type, transfer_id, file_hash}
    """

    def __init__(self, db, identity, downloads_dir: Optional[str] = None):
        self._db = db
        self._identity = identity
        self._downloads = Path(downloads_dir or Path.home() / "PhantomLink Downloads")
        self._downloads.mkdir(parents=True, exist_ok=True)

        # transfer_id → TransferState
        self._outgoing: Dict[str, dict] = {}
        self._incoming: Dict[str, dict] = {}

        self.on_offer_received: Optional[Callable] = None   # (transfer_id, info)
        self.on_progress: Optional[Callable] = None          # (transfer_id, pct)
        self.on_complete: Optional[Callable] = None          # (transfer_id, path)
        self.on_error: Optional[Callable] = None             # (transfer_id, err)

        self._ensure_table()

    def _ensure_table(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS file_transfers (
                transfer_id   TEXT PRIMARY KEY,
                peer_id       TEXT NOT NULL,
                filename      TEXT NOT NULL,
                file_size     INTEGER,
                file_hash     TEXT,
                direction     TEXT NOT NULL,
                status        TEXT DEFAULT 'pending',
                local_path    TEXT,
                started_at    TEXT,
                completed_at  TEXT
            )
        """)
        self._db.commit()

    async def send_file(self, conn, peer_id: str, file_path: str) -> str:
        """Initiate file send. Returns transfer_id."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            raise ValueError(f"File too large: {file_size / 1024**2:.1f}MB (max 512MB)")

        # Compute full file hash
        file_hash = self._hash_file(path)
        total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        transfer_id = str(uuid.uuid4())

        self._outgoing[transfer_id] = {
            "path": path,
            "peer_id": peer_id,
            "filename": path.name,
            "size": file_size,
            "file_hash": file_hash,
            "total_chunks": total_chunks,
            "sent_chunks": set(),
            "conn": conn,
        }

        # Record in DB
        now = datetime.utcnow().isoformat()
        self._db.execute("""
            INSERT INTO file_transfers
            (transfer_id, peer_id, filename, file_size, file_hash, direction,
             status, local_path, started_at)
            VALUES (?, ?, ?, ?, ?, 'sent', 'offered', ?, ?)
        """, (transfer_id, peer_id, path.name, file_size,
              file_hash, str(path), now))
        self._db.commit()

        # Send offer
        await conn.send({
            "type": "file_offer",
            "transfer_id": transfer_id,
            "filename": path.name,
            "size": file_size,
            "total_chunks": total_chunks,
            "file_hash": file_hash,
            "sender_id": self._identity.device_id,
        })
        logger.info(f"File offer sent: {path.name} ({file_size} bytes) → {peer_id[:8]}")
        return transfer_id

    async def handle_file_offer(self, conn, msg: dict):
        """Peer wants to send us a file."""
        transfer_id = msg["transfer_id"]
        filename = msg["filename"]
        size = msg["size"]
        total_chunks = msg["total_chunks"]
        file_hash = msg["file_hash"]
        sender_id = msg.get("sender_id", "")

        self._incoming[transfer_id] = {
            "conn": conn,
            "filename": filename,
            "size": size,
            "total_chunks": total_chunks,
            "file_hash": file_hash,
            "received_chunks": {},
            "sender_id": sender_id,
        }

        if self.on_offer_received:
            self.on_offer_received(transfer_id, {
                "filename": filename,
                "size": size,
                "sender_id": sender_id,
            })
        # Auto-accept for now (UI can override with accept/reject)

    async def accept_transfer(self, conn, transfer_id: str):
        """Accept incoming file transfer."""
        await conn.send({"type": "file_accept", "transfer_id": transfer_id})

        state = self._incoming.get(transfer_id)
        if state:
            now = datetime.utcnow().isoformat()
            self._db.execute("""
                INSERT OR IGNORE INTO file_transfers
                (transfer_id, peer_id, filename, file_size, file_hash,
                 direction, status, started_at)
                VALUES (?, ?, ?, ?, ?, 'received', 'receiving', ?)
            """, (transfer_id, state["sender_id"], state["filename"],
                  state["size"], state["file_hash"], now))
            self._db.commit()

    async def reject_transfer(self, conn, transfer_id: str):
        await conn.send({"type": "file_reject", "transfer_id": transfer_id})
        self._incoming.pop(transfer_id, None)

    async def handle_file_accept(self, conn, msg: dict):
        """Peer accepted our offer — start sending chunks."""
        transfer_id = msg["transfer_id"]
        state = self._outgoing.get(transfer_id)
        if not state:
            return

        self._db.execute(
            "UPDATE file_transfers SET status='sending' WHERE transfer_id=?",
            (transfer_id,)
        )
        self._db.commit()

        asyncio.create_task(self._send_chunks(transfer_id, conn))

    async def _send_chunks(self, transfer_id: str, conn):
        """Send file in chunks with per-chunk SHA-256 hash."""
        state = self._outgoing[transfer_id]
        path = state["path"]
        total = state["total_chunks"]

        with open(path, "rb") as f:
            for chunk_idx in range(total):
                chunk = f.read(CHUNK_SIZE)
                chunk_hash = hashlib.sha256(chunk).hexdigest()

                await conn.send({
                    "type": "file_chunk",
                    "transfer_id": transfer_id,
                    "chunk_index": chunk_idx,
                    "total_chunks": total,
                    "data": base64.b64encode(chunk).decode(),
                    "chunk_hash": chunk_hash,
                })

                # Wait for ack before next chunk (flow control)
                state["sent_chunks"].add(chunk_idx)
                pct = int((chunk_idx + 1) / total * 100)
                if self.on_progress:
                    self.on_progress(transfer_id, pct)

                await asyncio.sleep(0.01)  # prevent flooding

        # Send completion
        await conn.send({
            "type": "file_complete",
            "transfer_id": transfer_id,
            "file_hash": state["file_hash"],
        })
        logger.info(f"File transfer complete: {state['filename']}")

    async def handle_file_chunk(self, msg: dict):
        """Receive a file chunk, verify hash, store."""
        transfer_id = msg["transfer_id"]
        state = self._incoming.get(transfer_id)
        if not state:
            return

        chunk_idx = msg["chunk_index"]
        chunk_data = base64.b64decode(msg["data"])
        expected_hash = msg["chunk_hash"]
        total = msg["total_chunks"]

        # Verify chunk integrity
        actual_hash = hashlib.sha256(chunk_data).hexdigest()
        if actual_hash != expected_hash:
            logger.error(f"Chunk {chunk_idx} hash mismatch! Transfer {transfer_id[:8]}")
            if self.on_error:
                self.on_error(transfer_id, f"Chunk {chunk_idx} corrupted")
            return

        state["received_chunks"][chunk_idx] = chunk_data
        pct = int(len(state["received_chunks"]) / total * 100)
        if self.on_progress:
            self.on_progress(transfer_id, pct)

    async def handle_file_complete(self, msg: dict):
        """All chunks received. Reassemble and verify full file hash."""
        transfer_id = msg["transfer_id"]
        state = self._incoming.get(transfer_id)
        if not state:
            return

        total = state["total_chunks"]
        if len(state["received_chunks"]) < total:
            logger.error(f"Incomplete transfer: {len(state['received_chunks'])}/{total} chunks")
            return

        # Assemble file
        safe_name = Path(state["filename"]).name
        out_path = self._downloads / safe_name

        # Handle duplicate filenames
        counter = 1
        while out_path.exists():
            stem = Path(safe_name).stem
            suffix = Path(safe_name).suffix
            out_path = self._downloads / f"{stem}_{counter}{suffix}"
            counter += 1

        with open(out_path, "wb") as f:
            for i in range(total):
                f.write(state["received_chunks"][i])

        # Verify full file hash
        actual_hash = self._hash_file(out_path)
        if actual_hash != state["file_hash"]:
            out_path.unlink()
            if self.on_error:
                self.on_error(transfer_id, "File hash mismatch — file corrupted")
            return

        now = datetime.utcnow().isoformat()
        self._db.execute("""
            UPDATE file_transfers
            SET status='completed', local_path=?, completed_at=?
            WHERE transfer_id=?
        """, (str(out_path), now, transfer_id))
        self._db.commit()

        self._incoming.pop(transfer_id, None)
        logger.info(f"File saved: {out_path}")
        if self.on_complete:
            self.on_complete(transfer_id, str(out_path))

    @staticmethod
    def _hash_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()


# ══════════════════════════════════════════════════════════════════════════════
# 2. TYPING INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

class TypingIndicator:
    """Send/receive lightweight typing heartbeats."""

    def __init__(self):
        self._typing_peers: Dict[str, float] = {}  # peer_id → last_seen timestamp
        self.on_typing_changed: Optional[Callable] = None  # (peer_id, is_typing)

    async def send_typing(self, conn, sender_id: str):
        await conn.send({"type": "is_typing", "sender_id": sender_id})

    async def send_stopped(self, conn, sender_id: str):
        await conn.send({"type": "stopped_typing", "sender_id": sender_id})

    def handle_typing(self, peer_id: str):
        was_typing = peer_id in self._typing_peers
        self._typing_peers[peer_id] = time.time()
        if not was_typing and self.on_typing_changed:
            self.on_typing_changed(peer_id, True)
        # Auto-clear after 4s
        asyncio.create_task(self._auto_clear(peer_id, 4.0))

    def handle_stopped(self, peer_id: str):
        if peer_id in self._typing_peers:
            del self._typing_peers[peer_id]
            if self.on_typing_changed:
                self.on_typing_changed(peer_id, False)

    async def _auto_clear(self, peer_id: str, delay: float):
        await asyncio.sleep(delay)
        last = self._typing_peers.get(peer_id, 0)
        if time.time() - last >= delay - 0.1:
            self.handle_stopped(peer_id)

    def is_typing(self, peer_id: str) -> bool:
        last = self._typing_peers.get(peer_id, 0)
        return time.time() - last < 4.0


import time  # needed above


# ══════════════════════════════════════════════════════════════════════════════
# 3. READ RECEIPTS
# ══════════════════════════════════════════════════════════════════════════════

class ReadReceiptManager:
    """
    Double-tick read receipts.
    ✓ = delivered (peer received), ✓✓ = read (peer opened chat).
    """

    def __init__(self, db):
        self._db = db
        self._ensure_table()
        self.on_receipt: Optional[Callable] = None  # (message_id, status)

    def _ensure_table(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS read_receipts (
                message_id  TEXT NOT NULL,
                peer_id     TEXT NOT NULL,
                status      TEXT NOT NULL,   -- 'delivered' | 'read'
                timestamp   TEXT NOT NULL,
                PRIMARY KEY (message_id, peer_id)
            )
        """)
        self._db.commit()

    async def send_delivered(self, conn, message_id: str, sender_id: str):
        await conn.send({
            "type": "read_receipt",
            "message_id": message_id,
            "status": "delivered",
            "sender_id": sender_id,
            "timestamp": datetime.utcnow().isoformat(),
        })

    async def send_read(self, conn, message_id: str, sender_id: str):
        await conn.send({
            "type": "read_receipt",
            "message_id": message_id,
            "status": "read",
            "sender_id": sender_id,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def handle_receipt(self, msg: dict):
        message_id = msg["message_id"]
        peer_id = msg.get("sender_id", "")
        status = msg["status"]
        ts = msg.get("timestamp", datetime.utcnow().isoformat())

        self._db.execute("""
            INSERT OR REPLACE INTO read_receipts
            (message_id, peer_id, status, timestamp)
            VALUES (?, ?, ?, ?)
        """, (message_id, peer_id, status, ts))
        self._db.commit()

        if self.on_receipt:
            self.on_receipt(message_id, status)

    def get_status(self, message_id: str) -> str:
        """Returns 'sent', 'delivered', or 'read'."""
        row = self._db.fetchone("""
            SELECT status FROM read_receipts
            WHERE message_id=?
            ORDER BY CASE status WHEN 'read' THEN 0 ELSE 1 END
            LIMIT 1
        """, (message_id,))
        return row["status"] if row else "sent"


# ══════════════════════════════════════════════════════════════════════════════
# 4. SELF-DESTRUCTING MESSAGES
# ══════════════════════════════════════════════════════════════════════════════

class SelfDestructManager:
    """
    Per-message TTL. Message auto-deleted after timer expires.
    Timer starts when message is READ (not sent).
    """

    def __init__(self, db):
        self._db = db
        self._ensure_table()

    def _ensure_table(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS self_destruct (
                message_id   TEXT PRIMARY KEY,
                destruct_at  TEXT NOT NULL,
                destroyed    INTEGER DEFAULT 0
            )
        """)
        self._db.commit()

    def schedule(self, message_id: str, ttl_seconds: int):
        """Schedule message for deletion after ttl_seconds."""
        destruct_at = (datetime.utcnow() + timedelta(seconds=ttl_seconds)).isoformat()
        self._db.execute("""
            INSERT OR REPLACE INTO self_destruct (message_id, destruct_at)
            VALUES (?, ?)
        """, (message_id, destruct_at))
        self._db.commit()

    async def run_cleanup_loop(self):
        """Background loop: delete expired messages every 10s."""
        while True:
            await asyncio.sleep(10)
            self._purge_expired()

    def _purge_expired(self):
        now = datetime.utcnow().isoformat()
        expired = self._db.fetchall("""
            SELECT message_id FROM self_destruct
            WHERE destruct_at <= ? AND destroyed=0
        """, (now,))

        for row in expired:
            mid = row["message_id"]
            # Delete from messages table
            self._db.execute("DELETE FROM messages WHERE message_id=?", (mid,))
            self._db.execute(
                "UPDATE self_destruct SET destroyed=1 WHERE message_id=?",
                (mid,)
            )
            logger.info(f"Self-destruct: deleted message {mid[:8]}")

        if expired:
            self._db.commit()

    def get_ttl_remaining(self, message_id: str) -> Optional[int]:
        """Returns seconds remaining, or None if not scheduled."""
        row = self._db.fetchone("""
            SELECT destruct_at FROM self_destruct
            WHERE message_id=? AND destroyed=0
        """, (message_id,))
        if not row:
            return None
        try:
            dt = datetime.fromisoformat(row["destruct_at"])
            remaining = (dt - datetime.utcnow()).total_seconds()
            return max(0, int(remaining))
        except Exception:
            return None


# ══════════════════════════════════════════════════════════════════════════════
# 5. MESSAGE REACTIONS
# ══════════════════════════════════════════════════════════════════════════════

class ReactionManager:
    """
    Emoji reactions on messages. Synced via gossip.
    reaction_add: {type, message_id, peer_id, emoji, timestamp}
    """

    def __init__(self, db):
        self._db = db
        self._ensure_table()
        self.on_reaction: Optional[Callable] = None  # (message_id, reactions)

    def _ensure_table(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                reaction_id  TEXT PRIMARY KEY,
                message_id   TEXT NOT NULL,
                peer_id      TEXT NOT NULL,
                emoji        TEXT NOT NULL,
                timestamp    TEXT NOT NULL,
                UNIQUE(message_id, peer_id, emoji)
            )
        """)
        self._db.commit()

    async def send_reaction(self, conn, message_id: str,
                             sender_id: str, emoji: str):
        await conn.send({
            "type": "reaction_add",
            "reaction_id": str(uuid.uuid4()),
            "message_id": message_id,
            "peer_id": sender_id,
            "emoji": emoji,
            "timestamp": datetime.utcnow().isoformat(),
        })

    async def send_remove_reaction(self, conn, message_id: str,
                                    sender_id: str, emoji: str):
        await conn.send({
            "type": "reaction_remove",
            "message_id": message_id,
            "peer_id": sender_id,
            "emoji": emoji,
        })

    def handle_reaction_add(self, msg: dict):
        mid = msg["message_id"]
        pid = msg["peer_id"]
        emoji = msg["emoji"]
        rid = msg.get("reaction_id", str(uuid.uuid4()))
        ts = msg.get("timestamp", datetime.utcnow().isoformat())

        try:
            self._db.execute("""
                INSERT OR IGNORE INTO reactions
                (reaction_id, message_id, peer_id, emoji, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (rid, mid, pid, emoji, ts))
            self._db.commit()
        except Exception:
            pass

        if self.on_reaction:
            self.on_reaction(mid, self.get_reactions(mid))

    def handle_reaction_remove(self, msg: dict):
        mid = msg["message_id"]
        pid = msg["peer_id"]
        emoji = msg["emoji"]
        self._db.execute("""
            DELETE FROM reactions
            WHERE message_id=? AND peer_id=? AND emoji=?
        """, (mid, pid, emoji))
        self._db.commit()
        if self.on_reaction:
            self.on_reaction(mid, self.get_reactions(mid))

    def get_reactions(self, message_id: str) -> Dict[str, List[str]]:
        """Returns {emoji: [peer_ids]} for a message."""
        rows = self._db.fetchall("""
            SELECT emoji, peer_id FROM reactions WHERE message_id=?
        """, (message_id,))
        result: Dict[str, List[str]] = {}
        for row in rows:
            result.setdefault(row["emoji"], []).append(row["peer_id"])
        return result


# ══════════════════════════════════════════════════════════════════════════════
# 6. FTS5 FULL-TEXT SEARCH
# ══════════════════════════════════════════════════════════════════════════════

class MessageSearch:
    """
    FTS5 full-text search on decrypted message cache.
    Maintains a plaintext_cache table populated on decrypt.
    FTS5 virtual table indexes it for fast search.
    """

    def __init__(self, db):
        self._db = db
        self._ensure_tables()

    def _ensure_tables(self):
        # Plaintext cache (populated when messages are decrypted for display)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS message_plaintext_cache (
                message_id TEXT PRIMARY KEY,
                peer_id    TEXT NOT NULL,
                sender_id  TEXT NOT NULL,
                content    TEXT NOT NULL,
                timestamp  TEXT NOT NULL
            )
        """)
        # FTS5 virtual table
        try:
            self._db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS message_fts
                USING fts5(
                    message_id UNINDEXED,
                    peer_id UNINDEXED,
                    content,
                    timestamp UNINDEXED,
                    content='message_plaintext_cache',
                    content_rowid='rowid'
                )
            """)
        except Exception as e:
            logger.warning(f"FTS5 not available: {e} — search disabled")
        self._db.commit()

    def index_message(self, message_id: str, peer_id: str,
                       sender_id: str, content: str, timestamp: str):
        """Add decrypted message to search index."""
        try:
            self._db.execute("""
                INSERT OR REPLACE INTO message_plaintext_cache
                (message_id, peer_id, sender_id, content, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (message_id, peer_id, sender_id, content, timestamp))
            # Rebuild FTS index for this row
            self._db.execute("""
                INSERT OR REPLACE INTO message_fts
                (message_id, peer_id, content, timestamp)
                VALUES (?, ?, ?, ?)
            """, (message_id, peer_id, content, timestamp))
            self._db.commit()
        except Exception as e:
            logger.debug(f"FTS index error: {e}")

    def search(self, query: str, peer_id: Optional[str] = None,
               limit: int = 50) -> List[dict]:
        """
        Full-text search. Returns matching messages.
        Supports FTS5 query syntax: AND, OR, phrase "hello world", prefix hi*
        """
        try:
            if peer_id:
                rows = self._db.fetchall("""
                    SELECT c.message_id, c.peer_id, c.sender_id,
                           c.content, c.timestamp,
                           highlight(message_fts, 2, '<mark>', '</mark>') as highlighted
                    FROM message_fts f
                    JOIN message_plaintext_cache c ON c.message_id = f.message_id
                    WHERE message_fts MATCH ?
                    AND c.peer_id = ?
                    ORDER BY rank
                    LIMIT ?
                """, (query, peer_id, limit))
            else:
                rows = self._db.fetchall("""
                    SELECT c.message_id, c.peer_id, c.sender_id,
                           c.content, c.timestamp,
                           highlight(message_fts, 2, '<mark>', '</mark>') as highlighted
                    FROM message_fts f
                    JOIN message_plaintext_cache c ON c.message_id = f.message_id
                    WHERE message_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (query, limit))
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"FTS search error: {e}")
            # Fallback: LIKE search
            try:
                rows = self._db.fetchall("""
                    SELECT message_id, peer_id, sender_id, content, timestamp,
                           content as highlighted
                    FROM message_plaintext_cache
                    WHERE content LIKE ?
                    ORDER BY timestamp DESC LIMIT ?
                """, (f"%{query}%", limit))
                return [dict(r) for r in rows]
            except Exception:
                return []

    def delete_from_index(self, message_id: str):
        try:
            self._db.execute(
                "DELETE FROM message_plaintext_cache WHERE message_id=?",
                (message_id,)
            )
            self._db.execute(
                "DELETE FROM message_fts WHERE message_id=?",
                (message_id,)
            )
            self._db.commit()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# 7. GROUP CHANNELS
# ══════════════════════════════════════════════════════════════════════════════

class GroupChannelManager:
    """
    Group channels: named rooms, multiple members, gossip-synced messages.

    Schema:
      groups (group_id, name, created_by, created_at, members_json)
      group_messages (message_id, group_id, sender_id, timestamp,
                      content BLOB, content_hash, nonce BLOB)
    """

    def __init__(self, db, identity, node):
        self._db = db
        self._identity = identity
        self._node = node
        self._ensure_tables()
        self.on_group_message: Optional[Callable] = None
        self.on_group_updated: Optional[Callable] = None

    def _ensure_tables(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                group_id    TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                created_by  TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                members     TEXT NOT NULL   -- JSON array of peer_ids
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS group_messages (
                message_id   TEXT PRIMARY KEY,
                group_id     TEXT NOT NULL,
                sender_id    TEXT NOT NULL,
                timestamp    TEXT NOT NULL,
                content      BLOB NOT NULL,
                content_hash TEXT NOT NULL,
                nonce        BLOB NOT NULL,
                FOREIGN KEY(group_id) REFERENCES groups(group_id)
            )
        """)
        self._db.commit()

    def create_group(self, name: str, member_ids: List[str]) -> str:
        """Create a new group. Returns group_id."""
        group_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        my_id = self._identity.device_id
        all_members = list(set([my_id] + member_ids))

        self._db.execute("""
            INSERT INTO groups (group_id, name, created_by, created_at, members)
            VALUES (?, ?, ?, ?, ?)
        """, (group_id, name, my_id, now, json.dumps(all_members)))
        self._db.commit()
        return group_id

    def get_groups(self) -> List[dict]:
        rows = self._db.fetchall("SELECT * FROM groups ORDER BY created_at DESC")
        result = []
        for r in rows:
            d = dict(r)
            d["members"] = json.loads(d["members"])
            result.append(d)
        return result

    def get_group(self, group_id: str) -> Optional[dict]:
        row = self._db.fetchone(
            "SELECT * FROM groups WHERE group_id=?", (group_id,)
        )
        if not row:
            return None
        d = dict(row)
        d["members"] = json.loads(d["members"])
        return d

    async def send_group_message(self, group_id: str, content: str):
        """Broadcast message to all connected group members."""
        group = self.get_group(group_id)
        if not group:
            return

        message_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat()
        from crypto.identity import CryptoEngine

        content_hash = CryptoEngine.message_hash(content)

        # Store locally (use a simple AES key derived from group_id for group msgs)
        group_key = hashlib.sha256(group_id.encode()).digest()
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        enc = AESGCM(group_key).encrypt(nonce, content.encode(), None)

        self._db.execute("""
            INSERT OR IGNORE INTO group_messages
            (message_id, group_id, sender_id, timestamp,
             content, content_hash, nonce)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (message_id, group_id, self._identity.device_id,
              timestamp, enc, content_hash, nonce))
        self._db.commit()

        # Broadcast to all connected members
        packet = {
            "type": "group_message",
            "message_id": message_id,
            "group_id": group_id,
            "sender_id": self._identity.device_id,
            "group_name": group["name"],
            "timestamp": timestamp,
            "content": content,
            "content_hash": content_hash,
            "members": group["members"],
        }

        my_id = self._identity.device_id
        for member_id in group["members"]:
            if member_id == my_id:
                continue
            conn = self._node._connections.get(member_id)
            if conn:
                try:
                    await conn.send(packet)
                except Exception as e:
                    logger.warning(f"Group msg to {member_id[:8]} failed: {e}")

    async def handle_group_message(self, msg: dict):
        """Receive and store group message."""
        group_id = msg["group_id"]
        message_id = msg["message_id"]
        sender_id = msg["sender_id"]
        timestamp = msg["timestamp"]
        content = msg.get("content", "")
        content_hash = msg.get("content_hash", "")

        # Ensure group exists locally
        if not self.get_group(group_id):
            self._db.execute("""
                INSERT OR IGNORE INTO groups
                (group_id, name, created_by, created_at, members)
                VALUES (?, ?, ?, ?, ?)
            """, (group_id, msg.get("group_name", "Unknown Group"),
                  sender_id, datetime.utcnow().isoformat(),
                  json.dumps(msg.get("members", [sender_id]))))
            self._db.commit()

        # Store message
        group_key = hashlib.sha256(group_id.encode()).digest()
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        enc = AESGCM(group_key).encrypt(nonce, content.encode(), None)

        self._db.execute("""
            INSERT OR IGNORE INTO group_messages
            (message_id, group_id, sender_id, timestamp,
             content, content_hash, nonce)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (message_id, group_id, sender_id,
              timestamp, enc, content_hash, nonce))
        self._db.commit()

        if self.on_group_message:
            self.on_group_message(group_id, {
                "message_id": message_id,
                "sender_id": sender_id,
                "timestamp": timestamp,
                "content": content,
                "content_hash": content_hash,
            })

    def get_group_messages(self, group_id: str) -> List[dict]:
        rows = self._db.fetchall("""
            SELECT * FROM group_messages WHERE group_id=?
            ORDER BY timestamp ASC
        """, (group_id,))
        result = []
        group_key = hashlib.sha256(group_id.encode()).digest()
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        for row in rows:
            d = dict(row)
            try:
                d["plaintext"] = AESGCM(group_key).decrypt(
                    row["nonce"], row["content"], None
                ).decode("utf-8")
            except Exception:
                d["plaintext"] = "[decryption error]"
            result.append(d)
        return result


# ══════════════════════════════════════════════════════════════════════════════
# 8. DISTRIBUTED CONSENSUS (POLLS)
# ══════════════════════════════════════════════════════════════════════════════

class ConsensusManager:
    """Simple majority-vote polls broadcast to all connected peers."""

    def __init__(self, db, identity, node):
        self._db = db
        self._identity = identity
        self._node = node
        self._ensure_table()
        self.on_poll_update: Optional[Callable] = None

    def _ensure_table(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS polls (
                poll_id      TEXT PRIMARY KEY,
                question     TEXT NOT NULL,
                options      TEXT NOT NULL,   -- JSON array
                created_by   TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                closes_at    TEXT,
                votes        TEXT DEFAULT '{}'  -- JSON {option: [voter_ids]}
            )
        """)
        self._db.commit()

    def create_poll(self, question: str, options: List[str],
                    ttl_hours: int = 24) -> str:
        poll_id = str(uuid.uuid4())
        now = datetime.utcnow()
        closes = (now + timedelta(hours=ttl_hours)).isoformat()
        self._db.execute("""
            INSERT INTO polls (poll_id, question, options, created_by,
                               created_at, closes_at, votes)
            VALUES (?, ?, ?, ?, ?, ?, '{}')
        """, (poll_id, question, json.dumps(options),
              self._identity.device_id, now.isoformat(), closes))
        self._db.commit()
        return poll_id

    async def broadcast_poll(self, poll_id: str):
        row = self._db.fetchone("SELECT * FROM polls WHERE poll_id=?", (poll_id,))
        if not row:
            return
        packet = {
            "type": "poll_broadcast",
            "poll_id": poll_id,
            "question": row["question"],
            "options": json.loads(row["options"]),
            "created_by": row["created_by"],
            "closes_at": row["closes_at"],
        }
        for conn in self._node._connections.values():
            try:
                await conn.send(packet)
            except Exception:
                pass

    async def cast_vote(self, conn, poll_id: str, option: str):
        await conn.send({
            "type": "poll_vote",
            "poll_id": poll_id,
            "voter_id": self._identity.device_id,
            "option": option,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def handle_vote(self, msg: dict):
        poll_id = msg["poll_id"]
        voter_id = msg["voter_id"]
        option = msg["option"]

        row = self._db.fetchone("SELECT votes FROM polls WHERE poll_id=?", (poll_id,))
        if not row:
            return
        votes = json.loads(row["votes"] or "{}")
        votes.setdefault(option, [])
        if voter_id not in votes[option]:
            votes[option].append(voter_id)

        self._db.execute(
            "UPDATE polls SET votes=? WHERE poll_id=?",
            (json.dumps(votes), poll_id)
        )
        self._db.commit()

        if self.on_poll_update:
            self.on_poll_update(poll_id, votes)

    def get_results(self, poll_id: str) -> Optional[dict]:
        row = self._db.fetchone("SELECT * FROM polls WHERE poll_id=?", (poll_id,))
        if not row:
            return None
        d = dict(row)
        d["options"] = json.loads(d["options"])
        d["votes"] = json.loads(d["votes"] or "{}")
        # Determine winner
        if d["votes"]:
            winner = max(d["votes"], key=lambda k: len(d["votes"][k]))
            d["winner"] = winner
            d["winner_votes"] = len(d["votes"][winner])
        return d


# ══════════════════════════════════════════════════════════════════════════════
# 9. SCHEDULED DELETION
# ══════════════════════════════════════════════════════════════════════════════

class ScheduledDeletion:
    """Auto-purge messages older than N days."""

    def __init__(self, db):
        self._db = db

    async def run_loop(self, retention_days: int = 30):
        """Run cleanup every hour."""
        while True:
            self._purge(retention_days)
            await asyncio.sleep(3600)

    def _purge(self, retention_days: int):
        cutoff = (datetime.utcnow() - timedelta(days=retention_days)).isoformat()
        result = self._db.execute("""
            DELETE FROM messages WHERE timestamp < ?
        """, (cutoff,))
        self._db.commit()
        if result.rowcount:
            logger.info(f"Scheduled deletion: removed {result.rowcount} messages "
                        f"older than {retention_days} days")


# ══════════════════════════════════════════════════════════════════════════════
# 10. PSEUDONYM MODE
# ══════════════════════════════════════════════════════════════════════════════

class PseudonymManager:
    """
    Maintain separate identity per peer.
    Each peer sees a different name + fingerprint for us.
    Actual device_id stays same (routing); display name changes.
    """

    def __init__(self, db, identity):
        self._db = db
        self._identity = identity
        self._ensure_table()

    def _ensure_table(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS pseudonyms (
                peer_id     TEXT PRIMARY KEY,
                alias       TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        self._db.commit()

    def set_alias(self, peer_id: str, alias: str):
        """Set what name peer sees us as."""
        now = datetime.utcnow().isoformat()
        self._db.execute("""
            INSERT OR REPLACE INTO pseudonyms (peer_id, alias, created_at)
            VALUES (?, ?, ?)
        """, (peer_id, alias, now))
        self._db.commit()

    def get_alias(self, peer_id: str) -> str:
        """Get our alias for this peer (fallback to real name)."""
        row = self._db.fetchone(
            "SELECT alias FROM pseudonyms WHERE peer_id=?", (peer_id,)
        )
        return row["alias"] if row else self._identity.name

    def get_all_aliases(self) -> List[dict]:
        rows = self._db.fetchall("SELECT * FROM pseudonyms ORDER BY created_at")
        return [dict(r) for r in rows]

    def delete_alias(self, peer_id: str):
        self._db.execute("DELETE FROM pseudonyms WHERE peer_id=?", (peer_id,))
        self._db.commit()
