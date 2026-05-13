"""crypto/capsule.py - AES-GCM encrypted capsule export/import."""
import os, json, base64, struct
from datetime import datetime
from crypto.identity import CryptoEngine

MAGIC   = b"PHLK"
VERSION = 1


def _enc_bytes(obj):
    if isinstance(obj, (bytes, bytearray)):
        return base64.b64encode(obj).decode()
    raise TypeError(f"Not serializable: {type(obj)}")


def export_capsule(db, passphrase: str, output_path: str) -> dict:
    data = db.export_all()
    for msg in data.get("messages", []):
        for f in ("content", "nonce"):
            if isinstance(msg.get(f), (bytes, bytearray)):
                msg[f] = base64.b64encode(msg[f]).decode()
    for peer in data.get("peers", []):
        for f in ("public_key", "shared_secret"):
            if isinstance(peer.get(f), (bytes, bytearray)):
                peer[f] = base64.b64encode(peer[f]).decode()
    for ident in data.get("identity", []):
        for f in ("public_key", "private_key"):
            if isinstance(ident.get(f), (bytes, bytearray)):
                ident[f] = base64.b64encode(ident[f]).decode()

    meta = {"exported_at": datetime.utcnow().isoformat(),
            "peer_count": len(data.get("peers", [])),
            "msg_count":  len(data.get("messages", [])),
            "version": VERSION}
    data["_meta"] = meta
    plaintext = json.dumps(data, default=_enc_bytes).encode("utf-8")

    salt = os.urandom(16)
    key  = CryptoEngine.derive_storage_key(passphrase, salt)
    ciphertext, nonce = CryptoEngine.aes_encrypt(key, plaintext)

    with open(output_path, "wb") as f:
        f.write(MAGIC + struct.pack("B", VERSION) + salt + nonce + ciphertext)
    return meta


def import_capsule(db, passphrase: str, input_path: str) -> dict:
    with open(input_path, "rb") as f:
        raw = f.read()
    if raw[:4] != MAGIC:
        raise ValueError("Not a valid PhantomLink capsule file")
    version = struct.unpack("B", raw[4:5])[0]
    if version != VERSION:
        raise ValueError(f"Unsupported version: {version}")
    salt, nonce, ciphertext = raw[5:21], raw[21:33], raw[33:]
    key = CryptoEngine.derive_storage_key(passphrase, salt)
    try:
        plaintext = CryptoEngine.aes_decrypt(key, ciphertext, nonce)
    except Exception:
        raise ValueError("Decryption failed — wrong passphrase or corrupted file")
    data = json.loads(plaintext.decode("utf-8"))
    meta = data.pop("_meta", {})
    for peer in data.get("peers", []):
        for f in ("public_key", "shared_secret"):
            if peer.get(f) and isinstance(peer[f], str):
                peer[f] = base64.b64decode(peer[f])
    for ident in data.get("identity", []):
        for f in ("public_key", "private_key"):
            if ident.get(f) and isinstance(ident[f], str):
                ident[f] = base64.b64decode(ident[f])
    db.import_all(data)
    return meta
