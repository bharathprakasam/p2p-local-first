# PhantomLink v2 ◈ Offline-First P2P Secure Mesh

Encrypted peer-to-peer communication. No servers. No accounts. No cloud.

## Quick Start

```bash
# 1. Create virtual environment
python -m venv .venv

# 2. Activate
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python main.py
```

## Project Structure

```
phantomlink_final/
├── main.py                   # Entry point — asyncio + Qt bridge
├── requirements.txt
├── README.md
├── crypto/
│   ├── __init__.py
│   ├── identity.py           # RSA-2048, AES-256-GCM, PBKDF2, signing
│   └── capsule.py            # Encrypted export/import (.plcap)
├── database/
│   ├── __init__.py
│   └── db_manager.py         # SQLite WAL — all tables + thread-safe ops
├── networking/
│   ├── __init__.py
│   └── node.py               # UDP discovery, TCP server, gossip protocol
└── ui/
    ├── __init__.py
    ├── theme.py              # All colors, QSS stylesheet
    ├── main_window.py        # Master orchestrator
    ├── radar_view.py         # Animated peer radar (60fps QPainter)
    ├── chat_view.py          # Message bubbles + all features
    ├── peers_sidebar.py      # Peer list with avatars + filter tabs
    ├── approval_dialog.py    # Connection approval + fingerprint
    ├── network_map.py        # Force-directed P2P topology graph
    ├── audit_view.py         # Real-time audit log
    └── capsule_view.py       # Backup/restore + Right to Forget
```

## Ports

| Port  | Protocol | Purpose                    |
|-------|----------|----------------------------|
| 47777 | UDP      | Device discovery broadcast |
| 47778 | TCP      | Peer connections + messaging |

## Features

### Security
- RSA-2048 zero-trust identity per device
- AES-256-GCM encryption at rest
- ECDH P-256 forward secrecy (new key every session)
- RSA-PSS message signing (non-repudiation)
- SHA-256 integrity hash on every message
- Right to Forget — schedule key deletion

### Networking
- UDP broadcast discovery (auto, every 5s)
- Gossip protocol sync (eventual consistency)
- Adaptive sync: full (<50 msgs) or partial (>=50)
- Store-and-forward relay support
- Auto-reconnect to trusted peers

### Messaging
- Real-time encrypted chat
- Typing indicators
- Read receipts (✓ delivered, ✓✓ read)
- Self-destructing messages (per-message TTL)
- Emoji reactions
- Reply threading
- File transfer (chunked + SHA-256 per chunk)
- Full-text search (FTS5)

### UI
- Animated radar with sweep + orbiting nodes
- Force-directed network topology graph
- Glassmorphism dark cyberpunk theme
- System tray + desktop notifications
- Peer filter tabs: All / Online / Trusted / Pending
- Capsule export/import with encryption status

## Firewall

```powershell
# Windows (run as Administrator):
netsh advfirewall firewall add rule name="PhantomLink UDP" protocol=UDP dir=in localport=47777 action=allow
netsh advfirewall firewall add rule name="PhantomLink TCP" protocol=TCP dir=in localport=47778 action=allow
```

```bash
# Linux:
sudo ufw allow 47777/udp
sudo ufw allow 47778/tcp
```

## Data Location

```
~/.phantomlink/phantomlink.db    # SQLite database (WAL mode)
~/PhantomLink Downloads/         # Received files
```
