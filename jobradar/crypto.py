import json
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    if settings.secret_key:
        return Fernet(settings.secret_key.encode())
    key_path = settings.data_dir / "secret.key"
    if not key_path.exists():
        key_path.write_bytes(Fernet.generate_key())
        os.chmod(key_path, 0o600)
    return Fernet(key_path.read_bytes().strip())


def encrypt_json(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str | None) -> dict:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except (InvalidToken, ValueError):
        return {}
