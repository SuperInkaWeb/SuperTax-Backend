"""
Configuración central de la plataforma.

Fuente única de verdad para toda la configuración, leída desde variables de
entorno (nunca secretos en código — regla de seguridad). Cada área de settings
se agrupa por responsabilidad. Los campos de Auth0/S3 se dejan preparados aquí
aunque su uso real empiece en fases posteriores.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ─── App ───
    APP_NAME: str = "Plataforma"
    ENV: str = "development"
    DEBUG: bool = True

    # ─── Base de datos (Postgres) ───
    DATABASE_URL: str = (
        "postgresql+psycopg2://plataforma:plataforma@localhost:5433/plataforma"
    )

    # ─── Redis (cola de jobs / cache) ───
    REDIS_URL: str = "redis://localhost:6379/0"

    # ─── Auth0 (identidad única — se usa desde la Fase 1) ───
    AUTH0_DOMAIN: str = ""
    AUTH0_AUDIENCE: str = ""

    # ─── Storage ───
    # Backend local (dev) hasta migrar a S3/R2. Los adaptadores se eligen en
    # platform/storage según estas variables.
    STORAGE_LOCAL_PATH: str = "./storage"
    S3_ENDPOINT_URL: str = ""
    S3_BUCKET: str = "plataforma"
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""

    # ─── Cifrado en reposo (Fernet) para datos sensibles (credenciales SUNAT) ───
    # DEV por defecto; en producción DEBE definirse por variable de entorno.
    ENCRYPTION_KEY: str = "v752OvalSjw6Lmo-cgJb12Kg7tGQ0qcIkdmnOMzcWj4="

    # ─── Jobs ───
    MAX_CONCURRENT_JOBS: int = 3

    # ─── CORS (orígenes del frontend permitidos) ───
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    """Instancia única (cacheada) de la configuración."""
    return Settings()


settings = get_settings()
