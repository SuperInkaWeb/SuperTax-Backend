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

    # ─── Auth0 (identidad única) ───
    AUTH0_DOMAIN: str = ""
    AUTH0_AUDIENCE: str = ""
    # SPA (para el email de establecer contraseña) y Management API (crear/borrar
    # usuarios desde el backend). Requeridos para el onboarding y la invitación.
    AUTH0_SPA_CLIENT_ID: str = ""
    AUTH0_MGMT_CLIENT_ID: str = ""
    AUTH0_MGMT_CLIENT_SECRET: str = ""
    AUTH0_DB_CONNECTION: str = "Username-Password-Authentication"

    # ─── Storage ───
    # STORAGE_BACKEND elige el adaptador explícitamente: "local" (disco, dev) o
    # "s3" (AWS S3 / Cloudflare R2 / MinIO). Explícito para no activar S3 por
    # error solo porque existan las variables S3_*.
    STORAGE_BACKEND: str = "local"
    STORAGE_LOCAL_PATH: str = "./storage"
    S3_ENDPOINT_URL: str = ""  # vacío = AWS; setear para R2/MinIO
    S3_REGION: str = "us-east-1"  # R2 usa "auto"
    S3_BUCKET: str = "plataforma"
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""

    # ─── Cifrado en reposo (Fernet) para datos sensibles (credenciales SUNAT) ───
    # DEV por defecto; en producción DEBE definirse por variable de entorno.
    ENCRYPTION_KEY: str = "v752OvalSjw6Lmo-cgJb12Kg7tGQ0qcIkdmnOMzcWj4="

    # ─── Jobs / SUNAT ───
    MAX_CONCURRENT_JOBS: int = 3
    SUNAT_POLL_TIMEOUT_MINUTES: int = 90
    # Descarga automatizada (módulo SUNAT / Playwright).
    DESCARGAS_DIR: str = ""  # vacío → carpeta temporal del SO
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    DRIVE_REDIRECT_URI: str = "http://localhost:8000/api/sunat/drive/callback"
    # Ejecutable de Tesseract OCR (módulo Scanner). En PATH por defecto.
    SCANNER_TESSERACT_CMD: str = "tesseract"

    # ─── CORS (orígenes del frontend permitidos) ───
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    """Instancia única (cacheada) de la configuración."""
    return Settings()


settings = get_settings()
