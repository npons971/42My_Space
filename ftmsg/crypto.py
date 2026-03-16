from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Union

from nacl import utils
from nacl.public import Box, PrivateKey, PublicKey


KeyLike = Union[bytes, str, PublicKey, PrivateKey]


def get_default_key_paths(base_dir: Path | None = None) -> tuple[Path, Path]:
    root = base_dir or (Path.home() / ".42msg" / "keys")
    return root / "enc_private.key", root / "enc_public.key"


def _coerce_public_key(key: KeyLike) -> PublicKey:
    if isinstance(key, PublicKey):
        return key
    if isinstance(key, bytes):
        return PublicKey(key)
    if isinstance(key, str):
        raw = base64.b64decode(key.encode("utf-8"))
        return PublicKey(raw)
    raise TypeError("target_pub_key must be PublicKey, bytes or base64 string")


def _coerce_private_key(key: KeyLike) -> PrivateKey:
    if isinstance(key, PrivateKey):
        return key
    if isinstance(key, bytes):
        return PrivateKey(key)
    if isinstance(key, str):
        raw = base64.b64decode(key.encode("utf-8"))
        return PrivateKey(raw)
    raise TypeError("private key must be PrivateKey, bytes or base64 string")


def generate_or_load_encryption_keypair(base_dir: Path | None = None) -> tuple[PrivateKey, PublicKey]:
    private_path, public_path = get_default_key_paths(base_dir)
    private_path.parent.mkdir(parents=True, exist_ok=True)

    if private_path.exists() and public_path.exists():
        private_raw = base64.b64decode(private_path.read_bytes())
        public_raw = base64.b64decode(public_path.read_bytes())
        private_key = PrivateKey(private_raw)
        public_key = PublicKey(public_raw)
        if private_key.public_key != public_key:
            raise ValueError("Stored encryption key pair is inconsistent")
        return private_key, public_key

    private_key = PrivateKey.generate()
    public_key = private_key.public_key

    private_path.write_bytes(base64.b64encode(bytes(private_key)))
    public_path.write_bytes(base64.b64encode(bytes(public_key)))
    private_path.chmod(0o600)
    public_path.chmod(0o644)

    return private_key, public_key


def encrypt(message: Union[str, bytes], target_pub_key: KeyLike) -> str:
    target_key = _coerce_public_key(target_pub_key)
    sender_ephemeral_private = PrivateKey.generate()
    box = Box(sender_ephemeral_private, target_key)

    data = message.encode("utf-8") if isinstance(message, str) else message
    nonce = utils.random(Box.NONCE_SIZE)
    encrypted = box.encrypt(data, nonce)

    envelope = {
        "ephemeral_pub_key": base64.b64encode(bytes(sender_ephemeral_private.public_key)).decode("utf-8"),
        "nonce": base64.b64encode(encrypted.nonce).decode("utf-8"),
        "ciphertext": base64.b64encode(encrypted.ciphertext).decode("utf-8"),
    }
    return base64.b64encode(json.dumps(envelope, separators=(",", ":")).encode("utf-8")).decode("utf-8")


def decrypt(payload: str, my_private_key: KeyLike) -> bytes:
    private_key = _coerce_private_key(my_private_key)
    envelope_bytes = base64.b64decode(payload.encode("utf-8"))
    envelope = json.loads(envelope_bytes.decode("utf-8"))

    sender_pub_key = PublicKey(base64.b64decode(envelope["ephemeral_pub_key"].encode("utf-8")))
    nonce = base64.b64decode(envelope["nonce"].encode("utf-8"))
    ciphertext = base64.b64decode(envelope["ciphertext"].encode("utf-8"))

    box = Box(private_key, sender_pub_key)
    return box.decrypt(ciphertext, nonce)
