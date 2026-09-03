from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet

from agents_backend.config import Settings


def _fernet(settings: Settings) -> Fernet:
    secret = settings.bitrix24_credential_encryption_key
    if secret is None:
        raise RuntimeError("A chave de criptografia do Bitrix24 não está configurada")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.get_secret_value().encode()).digest())
    return Fernet(key)


def encrypt_secret(settings: Settings, value: str) -> str:
    return _fernet(settings).encrypt(value.encode()).decode()


def decrypt_secret(settings: Settings, ciphertext: str) -> str:
    return _fernet(settings).decrypt(ciphertext.encode()).decode()


def encrypt_json(settings: Settings, value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return encrypt_secret(settings, raw)


def decrypt_json(settings: Settings, ciphertext: str) -> dict[str, Any]:
    value = json.loads(decrypt_secret(settings, ciphertext))
    if not isinstance(value, dict):
        raise ValueError("payload Bitrix24 inválido")
    return value


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
