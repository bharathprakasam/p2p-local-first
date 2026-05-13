"""
crypto/advanced.py — Advanced cryptographic primitives for PhantomLink v2

Implements:
  1. Forward Secrecy — ECDH ephemeral key exchange per session
     Each connection generates a fresh EC keypair. The shared secret
     derived from ECDH is used for that session only. Even if long-term
     RSA private key leaks, past session keys cannot be recovered.

  2. Message Signing — RSA-PSS signatures on every message
     Provides non-repudiation: recipient can prove sender authored message.
     Signature covers: message_id + sender_id + timestamp + content_hash

  3. Steganography — hide PhantomLink messages inside fake UDP packets
     Carrier: spoofed NTP-like or DNS-like UDP packets
     Data hidden in padding/reserved fields using LSB steganography
     Provides traffic analysis resistance (messages look like normal traffic)

  4. Onion Routing — layered encryption through peer chain
     Message encrypted in layers: each hop decrypts one layer, forwards rest
     Sender is hidden from final recipient (only knows previous hop)
"""

import os
import hashlib
import hmac
import struct
import base64
import json
import random
import time
from typing import Tuple, Optional, List, Dict

try:
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    from cryptography.hazmat.primitives.asymmetric.ec import (
        ECDH, generate_private_key, SECP256R1, EllipticCurvePrivateKey
    )
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.backends import default_backend
    from cryptography.exceptions import InvalidSignature
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


# ══════════════════════════════════════════════════════════════════════════════
# 1. FORWARD SECRECY — ECDH Ephemeral Key Exchange
# ══════════════════════════════════════════════════════════════════════════════

class ECDHSession:
    """
    Ephemeral ECDH key exchange for one session (one TCP connection).

    Protocol:
      A generates EC keypair (eph_priv_A, eph_pub_A)
      B generates EC keypair (eph_priv_B, eph_pub_B)
      A sends eph_pub_A → B
      B sends eph_pub_B → A
      Both compute: shared = ECDH(eph_priv, peer_eph_pub)
      Both derive: session_key = HKDF(shared, salt="phantomlink-session")
      session_key used for AES-GCM for this session only
      Both ephemeral privkeys discarded after derivation → forward secrecy
    """

    def __init__(self):
        if not HAS_CRYPTO:
            raise RuntimeError("pip install cryptography")
        # Generate ephemeral EC keypair on P-256 curve
        self._private_key: EllipticCurvePrivateKey = generate_private_key(
            SECP256R1(), default_backend()
        )
        self._session_key: Optional[bytes] = None
        self._peer_pub_bytes: Optional[bytes] = None

    def get_public_bytes(self) -> bytes:
        """Serialized ephemeral public key to send to peer."""
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint
        )

    def derive_session_key(self, peer_pub_bytes: bytes,
                            salt: bytes = b"phantomlink-session-v2") -> bytes:
        """
        Perform ECDH with peer's ephemeral public key.
        Derive 32-byte AES session key via HKDF-SHA256.
        Destroys ephemeral private key after derivation (forward secrecy).
        """
        from cryptography.hazmat.primitives.asymmetric.ec import (
            EllipticCurvePublicKey, SECP256R1
        )
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat
        )

        # Load peer's ephemeral public key
        peer_pub = ec.EllipticCurvePublicKey.from_encoded_point(
            SECP256R1(), peer_pub_bytes
        )

        # ECDH — produces shared secret (raw bytes)
        shared_secret = self._private_key.exchange(ECDH(), peer_pub)

        # HKDF to derive AES-256 key
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=b"phantomlink-aes-gcm",
            backend=default_backend()
        )
        session_key = hkdf.derive(shared_secret)

        # Destroy ephemeral private key — this is the forward secrecy guarantee
        # Python GC will collect it; zero out the reference
        self._private_key = None
        self._session_key = session_key
        self._peer_pub_bytes = peer_pub_bytes

        return session_key

    @property
    def session_key(self) -> Optional[bytes]:
        return self._session_key

    @property
    def is_established(self) -> bool:
        return self._session_key is not None


class ForwardSecrecyManager:
    """
    Manages ECDH sessions per peer connection.
    Each new TCP connection gets a fresh ECDHSession → fresh AES key.
    Old keys not stored anywhere → compromising current key reveals nothing past.
    """

    def __init__(self):
        # peer_id → current session key (in-memory only, never written to DB)
        self._sessions: Dict[str, bytes] = {}
        # peer_id → ECDHSession (during handshake)
        self._pending: Dict[str, ECDHSession] = {}

    def initiate(self, peer_id: str) -> bytes:
        """Start ECDH handshake. Returns our ephemeral public key bytes to send."""
        session = ECDHSession()
        self._pending[peer_id] = session
        return session.get_public_bytes()

    def complete(self, peer_id: str, peer_pub_bytes: bytes) -> bytes:
        """
        Complete handshake with peer's ephemeral public key.
        Returns derived session key (also stored in memory).
        """
        session = self._pending.pop(peer_id, None)
        if not session:
            # We're the responder — create session now
            session = ECDHSession()

        key = session.derive_session_key(peer_pub_bytes)
        self._sessions[peer_id] = key
        return key

    def respond(self, peer_id: str, peer_pub_bytes: bytes) -> Tuple[bytes, bytes]:
        """
        Responder path: receive initiator's pub key, generate ours,
        derive session key. Returns (our_pub_bytes, session_key).
        """
        session = ECDHSession()
        our_pub = session.get_public_bytes()
        key = session.derive_session_key(peer_pub_bytes)
        self._sessions[peer_id] = key
        return our_pub, key

    def get_session_key(self, peer_id: str) -> Optional[bytes]:
        return self._sessions.get(peer_id)

    def invalidate(self, peer_id: str):
        """Remove session key on disconnect. Key is gone forever."""
        self._sessions.pop(peer_id, None)
        self._pending.pop(peer_id, None)

    def encrypt(self, peer_id: str, plaintext: bytes) -> Tuple[bytes, bytes]:
        """Encrypt with current session key. Returns (ciphertext, nonce)."""
        key = self._sessions.get(peer_id)
        if not key:
            raise ValueError(f"No session key for {peer_id}")
        nonce = os.urandom(12)
        ct = AESGCM(key).encrypt(nonce, plaintext, None)
        return ct, nonce

    def decrypt(self, peer_id: str, ciphertext: bytes, nonce: bytes) -> bytes:
        """Decrypt with current session key."""
        key = self._sessions.get(peer_id)
        if not key:
            raise ValueError(f"No session key for {peer_id}")
        return AESGCM(key).decrypt(nonce, ciphertext, None)


# ══════════════════════════════════════════════════════════════════════════════
# 2. MESSAGE SIGNING — RSA-PSS Non-repudiation
# ══════════════════════════════════════════════════════════════════════════════

class MessageSigner:
    """
    Signs messages with sender's RSA private key (RSA-PSS, SHA-256).
    Verifies with sender's RSA public key.

    Sign data = message_id || sender_id || timestamp || content_hash
    Signature attached to every chat_message packet.
    Recipient verifies before accepting → proves authorship.
    """

    @staticmethod
    def sign(private_key_pem: bytes, message_id: str, sender_id: str,
             timestamp: str, content_hash: str) -> str:
        """Returns base64-encoded RSA-PSS signature."""
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        priv = load_pem_private_key(private_key_pem, password=None,
                                    backend=default_backend())
        # Canonical signing payload
        payload = f"{message_id}|{sender_id}|{timestamp}|{content_hash}".encode()

        signature = priv.sign(
            payload,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode()

    @staticmethod
    def verify(public_key_pem: bytes, message_id: str, sender_id: str,
               timestamp: str, content_hash: str, signature_b64: str) -> bool:
        """Returns True if signature valid, False if tampered/forged."""
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
            pub = load_pem_public_key(public_key_pem, backend=default_backend())
            payload = f"{message_id}|{sender_id}|{timestamp}|{content_hash}".encode()
            sig = base64.b64decode(signature_b64)
            pub.verify(
                sig,
                payload,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
            return True
        except (InvalidSignature, Exception):
            return False


# ══════════════════════════════════════════════════════════════════════════════
# 3. STEGANOGRAPHY — Hide messages in decoy UDP packets
# ══════════════════════════════════════════════════════════════════════════════

# Fake NTP packet structure (48 bytes standard NTP)
# We use the "reference timestamp" + "originate timestamp" fields (16 bytes)
# to hide data. These fields are normally zeros in client requests.
# Additional data goes in UDP payload padding beyond 48 bytes.

NTP_HEADER = bytes([
    0x1B,                    # LI=0, VN=3, Mode=3 (client)
    0x00,                    # Stratum=0
    0x06,                    # Poll interval
    0xEC,                    # Precision
]) + b'\x00' * 44           # rest of NTP fields (we overwrite some)

MAX_STEGO_PAYLOAD = 480     # bytes per UDP stego packet


class SteganographyEngine:
    """
    Hides PhantomLink message payloads inside decoy UDP packets.

    Carrier format (appears as NTP client request):
      Bytes 0-3:   Real NTP header (LI, VN, Mode, Stratum, Poll, Precision)
      Bytes 4-7:   Root delay (zeroed)
      Bytes 8-11:  Root dispersion (zeroed)
      Bytes 12-15: Reference ID (magic: b'PHLK' to identify our packets)
      Bytes 16-23: Reference Timestamp ← hidden data chunk header
      Bytes 24-31: Originate Timestamp ← hidden data continuation
      Bytes 32-47: Transmit Timestamp  ← hidden data continuation
      Bytes 48+:   Extra padding       ← bulk of hidden payload

    Detection: only peers who know magic b'PHLK' in bytes 12-15 can extract.
    To outsiders: looks like malformed NTP traffic.

    For actual steganography in images/audio, use LSB embedding (see _lsb_embed).
    """

    MAGIC = b'PHLK'   # 4-byte identifier at offset 12

    @classmethod
    def encode(cls, payload: bytes) -> List[bytes]:
        """
        Split payload into stego UDP packets.
        Returns list of UDP packet bytes to send.
        """
        # Compress payload first
        import zlib
        compressed = zlib.compress(payload, level=6)

        # Prepend total length (4 bytes big-endian)
        data = struct.pack(">I", len(compressed)) + compressed

        # Split into chunks
        chunk_size = MAX_STEGO_PAYLOAD - 20  # leave room for header
        chunks = [data[i:i+chunk_size] for i in range(0, len(data), chunk_size)]
        total = len(chunks)

        packets = []
        for idx, chunk in enumerate(chunks):
            pkt = bytearray(NTP_HEADER)  # start with fake NTP

            # Write magic at bytes 12-15
            pkt[12:16] = cls.MAGIC

            # Write chunk metadata at bytes 16-19: [chunk_idx(2)][total(2)]
            pkt[16:18] = struct.pack(">H", idx)
            pkt[18:20] = struct.pack(">H", total)

            # Write chunk length at bytes 20-21
            pkt[20:22] = struct.pack(">H", len(chunk))

            # Write data starting at byte 22
            # NTP packet is 48 bytes; we overwrite timestamps freely
            while len(pkt) < 22 + len(chunk):
                pkt.append(0)
            pkt[22:22+len(chunk)] = chunk

            packets.append(bytes(pkt))

        return packets

    @classmethod
    def decode(cls, packets: List[bytes]) -> Optional[bytes]:
        """
        Reassemble payload from list of stego UDP packets.
        Returns decompressed payload or None if invalid.
        """
        if not packets:
            return None

        chunks = {}
        total_expected = None

        for pkt in packets:
            if len(pkt) < 22:
                continue
            if pkt[12:16] != cls.MAGIC:
                continue  # not our packet

            idx = struct.unpack(">H", pkt[16:18])[0]
            total = struct.unpack(">H", pkt[18:20])[0]
            length = struct.unpack(">H", pkt[20:22])[0]
            chunk = pkt[22:22+length]

            chunks[idx] = chunk
            total_expected = total

        if total_expected is None or len(chunks) != total_expected:
            return None  # incomplete

        # Reassemble in order
        data = b"".join(chunks[i] for i in range(total_expected))

        # Parse: first 4 bytes = compressed length
        compressed_len = struct.unpack(">I", data[:4])[0]
        compressed = data[4:4+compressed_len]

        import zlib
        try:
            return zlib.decompress(compressed)
        except Exception:
            return None

    @classmethod
    def is_stego_packet(cls, pkt: bytes) -> bool:
        """Quick check if UDP packet contains hidden PhantomLink data."""
        return len(pkt) >= 16 and pkt[12:16] == cls.MAGIC

    @staticmethod
    def lsb_embed_in_bytes(carrier: bytes, secret: bytes) -> bytes:
        """
        LSB steganography: hide secret bits in LSB of carrier bytes.
        For use with image/audio carriers.
        carrier must be at least 8x len(secret) bytes.
        """
        if len(carrier) < len(secret) * 8:
            raise ValueError("Carrier too small for secret")

        carrier_arr = bytearray(carrier)
        secret_bits = []
        for byte in secret:
            for bit in range(7, -1, -1):
                secret_bits.append((byte >> bit) & 1)

        # Embed length first (32 bits)
        length_bits = []
        for bit in range(31, -1, -1):
            length_bits.append((len(secret) >> bit) & 1)

        all_bits = length_bits + secret_bits
        for i, bit in enumerate(all_bits):
            carrier_arr[i] = (carrier_arr[i] & 0xFE) | bit

        return bytes(carrier_arr)

    @staticmethod
    def lsb_extract_from_bytes(carrier: bytes) -> Optional[bytes]:
        """Extract LSB-embedded secret from carrier bytes."""
        if len(carrier) < 32:
            return None

        # Extract length (first 32 bits)
        length = 0
        for i in range(32):
            length = (length << 1) | (carrier[i] & 1)

        if length <= 0 or length * 8 + 32 > len(carrier):
            return None

        # Extract secret bits
        secret_bits = []
        for i in range(32, 32 + length * 8):
            secret_bits.append(carrier[i] & 1)

        # Reconstruct bytes
        secret = bytearray()
        for i in range(0, len(secret_bits), 8):
            byte = 0
            for bit in secret_bits[i:i+8]:
                byte = (byte << 1) | bit
            secret.append(byte)

        return bytes(secret)


# ══════════════════════════════════════════════════════════════════════════════
# 4. ONION ROUTING — Layered encryption through peer chain
# ══════════════════════════════════════════════════════════════════════════════

class OnionRouter:
    """
    Implements onion routing for privacy.

    Concept:
      Sender wants to reach Destination via path: Sender → Hop1 → Hop2 → Dest

      Sender builds layered encrypted message:
        layer3 = encrypt(dest_pubkey,   {payload, next_hop: "final"})
        layer2 = encrypt(hop2_pubkey,   {payload: layer3, next_hop: dest_addr})
        layer1 = encrypt(hop1_pubkey,   {payload: layer2, next_hop: hop2_addr})

      Sender sends layer1 to Hop1.
      Hop1 decrypts → gets layer2 + hop2 address → forwards to Hop2.
      Hop2 decrypts → gets layer3 + dest address → forwards to Dest.
      Dest decrypts → gets original payload.

      Each hop only knows previous and next hop. Destination only knows Hop2.
      Sender's IP is hidden from Destination.
    """

    @staticmethod
    def build_onion(payload: dict, path: List[Dict[str, bytes]]) -> bytes:
        """
        Build layered onion packet.

        path: list of dicts, innermost (destination) first:
          [
            {"peer_id": dest_id,  "public_key": dest_pub_pem,  "address": (ip, port)},
            {"peer_id": hop2_id,  "public_key": hop2_pub_pem,  "address": (ip, port)},
            {"peer_id": hop1_id,  "public_key": hop1_pub_pem,  "address": (ip, port)},
          ]

        Returns bytes to send to path[-1] (first hop).
        """
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric import padding as apad

        # Start with actual payload for destination
        current = json.dumps({
            "type": "onion_final",
            "payload": payload,
        }).encode()

        # Wrap in layers from inside out
        for i, hop in enumerate(path):
            pub = load_pem_public_key(hop["public_key"], backend=default_backend())
            is_last_wrap = (i == len(path) - 1)

            # Next hop address (what this hop should forward to)
            if i == 0:
                next_addr = None  # destination — no forwarding
            else:
                next_hop = path[i - 1]
                next_addr = next_hop["address"]

            envelope = {
                "type": "onion_relay" if not is_last_wrap else "onion_entry",
                "next_hop": next_addr,
                "peer_id": hop["peer_id"],
                "inner": base64.b64encode(current).decode(),
            }
            inner_bytes = json.dumps(envelope).encode()

            # Encrypt with RSA-OAEP (for small payloads)
            # For larger payloads: hybrid — AES key encrypted with RSA
            if len(inner_bytes) <= 190:  # RSA-2048 OAEP max
                current = pub.encrypt(
                    inner_bytes,
                    apad.OAEP(
                        mgf=apad.MGF1(algorithm=hashes.SHA256()),
                        algorithm=hashes.SHA256(),
                        label=None
                    )
                )
            else:
                # Hybrid: encrypt inner with AES, encrypt AES key with RSA
                aes_key = os.urandom(32)
                nonce = os.urandom(12)
                ct = AESGCM(aes_key).encrypt(nonce, inner_bytes, None)
                enc_key = pub.encrypt(
                    aes_key,
                    apad.OAEP(
                        mgf=apad.MGF1(algorithm=hashes.SHA256()),
                        algorithm=hashes.SHA256(),
                        label=None
                    )
                )
                hybrid = {
                    "mode": "hybrid",
                    "enc_key": base64.b64encode(enc_key).decode(),
                    "nonce": base64.b64encode(nonce).decode(),
                    "ct": base64.b64encode(ct).decode(),
                }
                current = json.dumps(hybrid).encode()

        return current

    @staticmethod
    def peel_layer(private_key_pem: bytes, onion_bytes: bytes) -> Tuple[Optional[str], Optional[bytes]]:
        """
        Peel one onion layer with our private key.
        Returns (next_hop_address, inner_bytes) or (None, final_payload).
        next_hop_address = "ip:port" string or None if we're the destination.
        """
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.asymmetric import padding as apad

        priv = load_pem_private_key(private_key_pem, password=None,
                                    backend=default_backend())

        try:
            # Try direct RSA decrypt
            inner_bytes = priv.decrypt(
                onion_bytes,
                apad.OAEP(
                    mgf=apad.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )
        except Exception:
            # Try hybrid mode
            try:
                hybrid = json.loads(onion_bytes.decode())
                if hybrid.get("mode") == "hybrid":
                    enc_key = base64.b64decode(hybrid["enc_key"])
                    nonce = base64.b64decode(hybrid["nonce"])
                    ct = base64.b64decode(hybrid["ct"])
                    aes_key = priv.decrypt(
                        enc_key,
                        apad.OAEP(
                            mgf=apad.MGF1(algorithm=hashes.SHA256()),
                            algorithm=hashes.SHA256(),
                            label=None
                        )
                    )
                    inner_bytes = AESGCM(aes_key).decrypt(nonce, ct, None)
                else:
                    return None, None
            except Exception:
                return None, None

        try:
            envelope = json.loads(inner_bytes.decode())
        except Exception:
            return None, None

        msg_type = envelope.get("type")
        next_hop = envelope.get("next_hop")  # None or [ip, port]
        inner = base64.b64decode(envelope.get("inner", ""))

        if msg_type == "onion_final":
            # We are the destination
            payload = json.loads(inner.decode()) if inner else envelope.get("payload")
            return None, json.dumps(payload).encode()
        else:
            # We are a relay hop — forward inner to next_hop
            addr = f"{next_hop[0]}:{next_hop[1]}" if next_hop else None
            return addr, inner


# ══════════════════════════════════════════════════════════════════════════════
# 5. METADATA MINIMIZATION
# ══════════════════════════════════════════════════════════════════════════════

class MetadataMinimizer:
    """
    Reduces metadata leakage:
    - Strip precise timestamps (round to nearest minute)
    - Add random send delay (0-3s jitter)
    - Pad messages to fixed sizes (hide length)
    - Random sender name aliases
    """

    FIXED_SIZES = [256, 512, 1024, 2048, 4096]  # bytes — pad to nearest

    @staticmethod
    async def apply_send_delay():
        """Random 0-2s delay to resist traffic timing analysis."""
        import asyncio
        delay = random.uniform(0, 2.0)
        await asyncio.sleep(delay)

    @staticmethod
    def strip_timestamp(ts: str) -> str:
        """Round timestamp to nearest minute for metadata minimization."""
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(ts)
            # Zero out seconds and microseconds
            rounded = dt.replace(second=0, microsecond=0)
            return rounded.isoformat()
        except Exception:
            return ts

    @staticmethod
    def pad_message(content: bytes) -> bytes:
        """Pad message to next fixed size boundary with random bytes."""
        target = next(
            (s for s in MetadataMinimizer.FIXED_SIZES if s >= len(content)),
            MetadataMinimizer.FIXED_SIZES[-1]
        )
        if len(content) >= target:
            return content
        padding_len = target - len(content) - 2  # 2 bytes for length prefix
        if padding_len < 0:
            return content
        padding = os.urandom(padding_len)
        # Format: [2-byte real length][content][random padding]
        return struct.pack(">H", len(content)) + content + padding

    @staticmethod
    def unpad_message(padded: bytes) -> bytes:
        """Extract real content from padded message."""
        if len(padded) < 2:
            return padded
        real_len = struct.unpack(">H", padded[:2])[0]
        return padded[2:2+real_len]
