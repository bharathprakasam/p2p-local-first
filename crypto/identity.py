"""crypto/identity.py - RSA-2048 identity, AES-256-GCM, PBKDF2, KeyManager."""
import os, hashlib, hmac, base64, uuid, platform
from typing import Optional, Tuple

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature


class CryptoEngine:
    @staticmethod
    def generate_rsa_keypair() -> Tuple[bytes, bytes]:
        priv = rsa.generate_private_key(65537, 2048, default_backend())
        pub_pem  = priv.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        priv_pem = priv.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption())
        return pub_pem, priv_pem

    @staticmethod
    def fingerprint(public_key_pem: bytes) -> str:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        pub = load_pem_public_key(public_key_pem, backend=default_backend())
        der = pub.public_bytes(serialization.Encoding.DER,
                               serialization.PublicFormat.SubjectPublicKeyInfo)
        digest = hashlib.sha256(der).hexdigest().upper()
        return ":".join(digest[i:i+2] for i in range(0, 32, 2))

    @staticmethod
    def derive_storage_key(password: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(hashes.SHA256(), 32, salt, 200_000, default_backend())
        return kdf.derive(password.encode())

    @staticmethod
    def aes_encrypt(key: bytes, plaintext: bytes) -> Tuple[bytes, bytes]:
        nonce = os.urandom(12)
        return AESGCM(key).encrypt(nonce, plaintext, None), nonce

    @staticmethod
    def aes_decrypt(key: bytes, ciphertext: bytes, nonce: bytes) -> bytes:
        return AESGCM(key).decrypt(nonce, ciphertext, None)

    @staticmethod
    def message_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def verify_hash(content: str, expected: str) -> bool:
        return hmac.compare_digest(hashlib.sha256(content.encode()).hexdigest(), expected)

    @staticmethod
    def rsa_encrypt(public_key_pem: bytes, plaintext: bytes) -> bytes:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        pub = load_pem_public_key(public_key_pem, backend=default_backend())
        return pub.encrypt(plaintext, padding.OAEP(
            padding.MGF1(hashes.SHA256()), hashes.SHA256(), None))

    @staticmethod
    def rsa_decrypt(private_key_pem: bytes, ciphertext: bytes) -> bytes:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        priv = load_pem_private_key(private_key_pem, None, default_backend())
        return priv.decrypt(ciphertext, padding.OAEP(
            padding.MGF1(hashes.SHA256()), hashes.SHA256(), None))

    @staticmethod
    def rsa_sign(private_key_pem: bytes, data: bytes) -> bytes:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        priv = load_pem_private_key(private_key_pem, None, default_backend())
        return priv.sign(data, padding.PSS(padding.MGF1(hashes.SHA256()),
                                           padding.PSS.MAX_LENGTH), hashes.SHA256())

    @staticmethod
    def rsa_verify(public_key_pem: bytes, data: bytes, signature: bytes) -> bool:
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
            pub = load_pem_public_key(public_key_pem, backend=default_backend())
            pub.verify(signature, data, padding.PSS(padding.MGF1(hashes.SHA256()),
                                                    padding.PSS.MAX_LENGTH), hashes.SHA256())
            return True
        except Exception:
            return False

    @staticmethod
    def generate_shared_key() -> bytes:
        return os.urandom(32)


class KeyManager:
    def __init__(self, db, identity: "DeviceIdentity"):
        self._db = db
        self._identity = identity

    def get_or_create_shared_key(self, peer_id: str) -> Optional[bytes]:
        peer = self._db.get_peer(peer_id)
        if not peer or not peer["shared_secret"]:
            return None
        try:
            return CryptoEngine.rsa_decrypt(
                self._identity.get_private_key_pem(), peer["shared_secret"])
        except Exception:
            return None

    def establish_key_for_peer(self, peer_id: str, peer_public_key_pem: bytes) -> bytes:
        shared_key = CryptoEngine.generate_shared_key()
        our_pub    = self._identity.get_public_key_pem()
        enc_local  = CryptoEngine.rsa_encrypt(our_pub, shared_key)
        enc_peer   = CryptoEngine.rsa_encrypt(peer_public_key_pem, shared_key)
        self._db.upsert_peer(peer_id, name="", shared_secret=enc_local)
        return enc_peer

    def receive_shared_key(self, peer_id: str, encrypted_key: bytes):
        raw     = CryptoEngine.rsa_decrypt(self._identity.get_private_key_pem(), encrypted_key)
        our_pub = self._identity.get_public_key_pem()
        enc     = CryptoEngine.rsa_encrypt(our_pub, raw)
        self._db.upsert_peer(peer_id, name="", shared_secret=enc)


class DeviceIdentity:
    def __init__(self, db):
        self._db = db
        self._identity = None
        self._private_key_pem: Optional[bytes] = None
        self._load_or_create()

    def _load_or_create(self):
        row = self._db.get_identity()
        if row:
            self._identity      = dict(row)
            self._private_key_pem = row["private_key"]
        else:
            self._create_new()

    def _create_new(self):
        pub_pem, priv_pem = CryptoEngine.generate_rsa_keypair()
        device_id   = str(uuid.uuid4())
        name        = platform.node() or f"device-{device_id[:8]}"
        fingerprint = CryptoEngine.fingerprint(pub_pem)
        self._db.save_identity(device_id, name, pub_pem, priv_pem, fingerprint)
        self._private_key_pem = priv_pem
        self._identity = {"device_id": device_id, "name": name,
                          "public_key": pub_pem, "private_key": priv_pem,
                          "fingerprint": fingerprint}

    @property
    def device_id(self) -> str: return self._identity["device_id"]
    @property
    def name(self) -> str:      return self._identity["name"]
    @property
    def fingerprint(self) -> str: return self._identity["fingerprint"]

    def get_public_key_pem(self) -> bytes:  return self._identity["public_key"]
    def get_private_key_pem(self) -> bytes: return self._private_key_pem

    def to_announce_dict(self) -> dict:
        return {"device_id": self.device_id, "name": self.name,
                "fingerprint": self.fingerprint,
                "public_key": base64.b64encode(self.get_public_key_pem()).decode()}
