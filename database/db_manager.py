"""database/db_manager.py - Thread-safe SQLite backend."""
import sqlite3, threading, json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

DB_PATH = Path.home() / ".phantomlink" / "phantomlink.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS device_identity (
    id          INTEGER PRIMARY KEY,
    device_id   TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    public_key  BLOB NOT NULL,
    private_key BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    fingerprint TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS peers (
    peer_id       TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    public_key    BLOB,
    fingerprint   TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    last_seen     TEXT,
    last_ip       TEXT,
    last_port     INTEGER,
    shared_secret BLOB,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id   TEXT PRIMARY KEY,
    sender_id    TEXT NOT NULL,
    peer_id      TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    content      BLOB NOT NULL,
    content_hash TEXT NOT NULL,
    nonce        BLOB NOT NULL,
    synced       INTEGER DEFAULT 0,
    direction    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    peer_id    TEXT NOT NULL,
    message_id TEXT NOT NULL,
    synced_at  TEXT NOT NULL,
    PRIMARY KEY(peer_id, message_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    peer_id    TEXT,
    details    TEXT,
    timestamp  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS key_expiry (
    peer_id    TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relay_queue (
    relay_id       TEXT PRIMARY KEY,
    target_peer_id TEXT NOT NULL,
    sender_id      TEXT NOT NULL,
    payload        TEXT NOT NULL,
    stored_at      TEXT NOT NULL,
    delivered      INTEGER DEFAULT 0,
    ttl_hours      INTEGER DEFAULT 72
);

CREATE TABLE IF NOT EXISTS reactions (
    reaction_id TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL,
    peer_id     TEXT NOT NULL,
    emoji       TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    UNIQUE(message_id, peer_id, emoji)
);

CREATE TABLE IF NOT EXISTS read_receipts (
    message_id TEXT NOT NULL,
    peer_id    TEXT NOT NULL,
    status     TEXT NOT NULL,
    timestamp  TEXT NOT NULL,
    PRIMARY KEY(message_id, peer_id)
);

CREATE TABLE IF NOT EXISTS self_destruct (
    message_id  TEXT PRIMARY KEY,
    destruct_at TEXT NOT NULL,
    destroyed   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS groups (
    group_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    members    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_messages (
    message_id   TEXT PRIMARY KEY,
    group_id     TEXT NOT NULL,
    sender_id    TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    content      BLOB NOT NULL,
    content_hash TEXT NOT NULL,
    nonce        BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS pseudonyms (
    peer_id    TEXT PRIMARY KEY,
    alias      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class DatabaseManager:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False,
                                     detect_types=sqlite3.PARSE_DECLTYPES)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def execute(self, sql, params=()):
        with self._lock:
            return self._conn.execute(sql, params)

    def commit(self):
        with self._lock:
            self._conn.commit()

    def fetchone(self, sql, params=()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql, params=()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ── Identity ─────────────────────────────────────────────────────────────
    def save_identity(self, device_id, name, public_key, private_key, fingerprint):
        now = datetime.utcnow().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO device_identity "
                "(device_id,name,public_key,private_key,created_at,fingerprint) "
                "VALUES (?,?,?,?,?,?)",
                (device_id, name, public_key, private_key, now, fingerprint))
            self._conn.commit()

    def get_identity(self): return self.fetchone("SELECT * FROM device_identity LIMIT 1")

    # ── Peers ─────────────────────────────────────────────────────────────────
    def upsert_peer(self, peer_id, name, last_ip=None, last_port=None,
                    status=None, public_key=None, fingerprint=None, shared_secret=None):
        now = datetime.utcnow().isoformat()
        existing = self.fetchone("SELECT * FROM peers WHERE peer_id=?", (peer_id,))
        with self._lock:
            if not existing:
                self._conn.execute(
                    "INSERT INTO peers (peer_id,name,last_ip,last_port,status,"
                    "public_key,fingerprint,shared_secret,created_at,last_seen) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (peer_id, name, last_ip, last_port, status or 'pending',
                     public_key, fingerprint, shared_secret, now, now))
            else:
                updates = {"last_seen": now}
                if last_ip:       updates["last_ip"]       = last_ip
                if last_port:     updates["last_port"]     = last_port
                if status:        updates["status"]        = status
                if public_key:    updates["public_key"]    = public_key
                if fingerprint:   updates["fingerprint"]   = fingerprint
                if shared_secret: updates["shared_secret"] = shared_secret
                if name:          updates["name"]          = name
                cols = ", ".join(f"{k}=?" for k in updates)
                self._conn.execute(f"UPDATE peers SET {cols} WHERE peer_id=?",
                                   list(updates.values()) + [peer_id])
            self._conn.commit()

    def get_peer(self, peer_id): return self.fetchone("SELECT * FROM peers WHERE peer_id=?", (peer_id,))
    def get_trusted_peers(self): return self.fetchall("SELECT * FROM peers WHERE status='trusted'")
    def get_all_peers(self):     return self.fetchall("SELECT * FROM peers ORDER BY last_seen DESC")

    # ── Messages ──────────────────────────────────────────────────────────────
    def save_message(self, message_id, sender_id, peer_id, timestamp,
                     content, content_hash, nonce, direction, synced=0):
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO messages "
                "(message_id,sender_id,peer_id,timestamp,content,content_hash,nonce,direction,synced) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (message_id, sender_id, peer_id, timestamp,
                 content, content_hash, nonce, direction, synced))
            self._conn.commit()

    def get_messages_for_peer(self, peer_id):
        return self.fetchall("SELECT * FROM messages WHERE peer_id=? ORDER BY timestamp ASC", (peer_id,))

    def get_message_ids_for_peer(self, peer_id):
        rows = self.fetchall("SELECT message_id,timestamp FROM messages WHERE peer_id=?", (peer_id,))
        return [{"message_id": r["message_id"], "timestamp": r["timestamp"]} for r in rows]

    def get_message_by_id(self, message_id):
        return self.fetchone("SELECT * FROM messages WHERE message_id=?", (message_id,))

    def get_all_messages(self):
        return self.fetchall("SELECT * FROM messages ORDER BY timestamp ASC")

    def mark_synced(self, message_id, peer_id):
        now = datetime.utcnow().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sync_state (peer_id,message_id,synced_at) VALUES (?,?,?)",
                (peer_id, message_id, now))
            self._conn.execute("UPDATE messages SET synced=1 WHERE message_id=?", (message_id,))
            self._conn.commit()

    # ── Audit ─────────────────────────────────────────────────────────────────
    def log_event(self, event_type, peer_id=None, details=None):
        now = datetime.utcnow().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log (event_type,peer_id,details,timestamp) VALUES (?,?,?,?)",
                (event_type, peer_id, details, now))
            self._conn.commit()

    def get_audit_log(self, limit=200):
        return self.fetchall("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,))

    # ── Key expiry ────────────────────────────────────────────────────────────
    def set_key_expiry(self, peer_id, expires_at):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO key_expiry (peer_id,expires_at) VALUES (?,?)",
                (peer_id, expires_at))
            self._conn.commit()

    def get_expired_keys(self):
        now = datetime.utcnow().isoformat()
        return self.fetchall("SELECT * FROM key_expiry WHERE expires_at<=?", (now,))

    def delete_peer_key(self, peer_id):
        with self._lock:
            self._conn.execute("UPDATE peers SET shared_secret=NULL WHERE peer_id=?", (peer_id,))
            self._conn.execute("DELETE FROM key_expiry WHERE peer_id=?", (peer_id,))
            self._conn.commit()

    # ── Export/Import ─────────────────────────────────────────────────────────
    def export_all(self) -> Dict:
        return {
            "identity":   [dict(r) for r in self.fetchall("SELECT * FROM device_identity")],
            "peers":      [dict(r) for r in self.fetchall("SELECT * FROM peers")],
            "messages":   [dict(r) for r in self.fetchall("SELECT * FROM messages")],
            "sync_state": [dict(r) for r in self.fetchall("SELECT * FROM sync_state")],
            "audit_log":  [dict(r) for r in self.fetchall("SELECT * FROM audit_log")],
        }

    def import_all(self, data: Dict):
        import base64
        with self._lock:
            for row in data.get("peers", []):
                for f in ("public_key", "shared_secret"):
                    if isinstance(row.get(f), str): row[f] = base64.b64decode(row[f])
                self._conn.execute(
                    "INSERT OR IGNORE INTO peers "
                    "(peer_id,name,public_key,fingerprint,status,last_seen,"
                    "last_ip,last_port,shared_secret,created_at) "
                    "VALUES (:peer_id,:name,:public_key,:fingerprint,:status,"
                    ":last_seen,:last_ip,:last_port,:shared_secret,:created_at)", row)
            for row in data.get("messages", []):
                for f in ("content", "nonce"):
                    if isinstance(row.get(f), str): row[f] = base64.b64decode(row[f])
                self._conn.execute(
                    "INSERT OR IGNORE INTO messages "
                    "(message_id,sender_id,peer_id,timestamp,content,"
                    "content_hash,nonce,direction,synced) "
                    "VALUES (:message_id,:sender_id,:peer_id,:timestamp,:content,"
                    ":content_hash,:nonce,:direction,:synced)", row)
            self._conn.commit()
