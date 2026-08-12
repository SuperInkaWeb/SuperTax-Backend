"""
Cifrado simétrico (Fernet / AES-128-CBC + HMAC) para datos sensibles en reposo.

Hoy lo usan las credenciales SUNAT de cada empresa (clave SOL, client_secret),
que nunca se guardan en texto plano. La clave viene solo de entorno (settings).
"""
from cryptography.fernet import Fernet

from src.platform.config.settings import settings

_fernet = Fernet(settings.ENCRYPTION_KEY.encode())


def encrypt_field(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt_field(encrypted: str) -> str:
    return _fernet.decrypt(encrypted.encode()).decode()
