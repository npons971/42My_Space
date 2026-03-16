from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from nacl.signing import SigningKey, VerifyKey


def get_default_signing_key_paths(base_dir: Path | None = None) -> tuple[Path, Path]:
    root = base_dir or (Path.home() / ".42msg" / "keys")
    return root / "sign_private.key", root / "sign_public.key"


def generate_or_load_signing_keypair(base_dir: Path | None = None) -> tuple[SigningKey, VerifyKey]:
    private_path, public_path = get_default_signing_key_paths(base_dir)
    private_path.parent.mkdir(parents=True, exist_ok=True)

    if private_path.exists() and public_path.exists():
        private_raw = base64.b64decode(private_path.read_bytes())
        public_raw = base64.b64decode(public_path.read_bytes())
        signing_key = SigningKey(private_raw)
        verify_key = VerifyKey(public_raw)
        if signing_key.verify_key != verify_key:
            raise ValueError("Stored signing key pair is inconsistent")
        return signing_key, verify_key

    signing_key = SigningKey.generate()
    verify_key = signing_key.verify_key

    private_path.write_bytes(base64.b64encode(bytes(signing_key)))
    public_path.write_bytes(base64.b64encode(bytes(verify_key)))
    private_path.chmod(0o600)
    public_path.chmod(0o644)
    return signing_key, verify_key


def canonical_signature_payload(frame: dict[str, Any]) -> bytes:
    to_sign = {
        "sender_login": frame["sender_login"],
        "timestamp": frame["timestamp"],
        "type": frame["type"],
        "payload": frame["payload"],
    }
    return json.dumps(to_sign, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_frame(frame: dict[str, Any], signing_key: SigningKey) -> str:
    signed = signing_key.sign(canonical_signature_payload(frame))
    return base64.b64encode(signed.signature).decode("utf-8")


def verify_frame_signature(frame: dict[str, Any], verify_key_b64: str) -> bool:
    try:
        verify_key = VerifyKey(base64.b64decode(verify_key_b64.encode("utf-8")))
        signature = base64.b64decode(frame["signature"].encode("utf-8"))
        verify_key.verify(canonical_signature_payload(frame), signature)
        return True
    except Exception:
        return False
