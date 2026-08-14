"""
Tests de la configuración: la llave de cifrado por defecto no se permite en
producción (endurecimiento de secretos en reposo).
"""
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from src.platform.config.settings import _DEFAULT_DEV_ENCRYPTION_KEY, Settings


def test_encryption_key_default_falla_en_produccion():
    with pytest.raises(ValidationError):
        Settings(ENV="production", ENCRYPTION_KEY=_DEFAULT_DEV_ENCRYPTION_KEY)


def test_encryption_key_vacia_falla_en_produccion():
    with pytest.raises(ValidationError):
        Settings(ENV="production", ENCRYPTION_KEY="")


def test_encryption_key_propia_en_produccion_ok():
    propia = Fernet.generate_key().decode()
    settings = Settings(ENV="production", ENCRYPTION_KEY=propia)
    assert settings.ENCRYPTION_KEY == propia


def test_encryption_key_default_ok_en_desarrollo():
    settings = Settings(ENV="development", ENCRYPTION_KEY=_DEFAULT_DEV_ENCRYPTION_KEY)
    assert settings.ENV == "development"
