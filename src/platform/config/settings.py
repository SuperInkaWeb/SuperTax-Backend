"""
Configuración central de la plataforma.

Fuente única de verdad para toda la configuración, leída desde variables de
entorno (nunca secretos en código — regla de seguridad). Cada área de settings
se agrupa por responsabilidad. Los campos de Auth0/S3 se dejan preparados aquí
aunque su uso real empiece en fases posteriores.
"""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Llave Fernet de desarrollo. Cómoda en local, pero está en el repo: en
# producción DEBE reemplazarse por una propia (ver el validador más abajo).
_DEFAULT_DEV_ENCRYPTION_KEY = "v752OvalSjw6Lmo-cgJb12Kg7tGQ0qcIkdmnOMzcWj4="


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
    ENCRYPTION_KEY: str = _DEFAULT_DEV_ENCRYPTION_KEY

    # ─── Jobs / SUNAT ───
    SUNAT_POLL_TIMEOUT_MINUTES: int = 90
    # Modelo on-demand: la descarga se ejecuta dentro del proceso web en un pool
    # de hilos acotado (no un worker que sondea 24/7). Este número es cuántas
    # descargas SUNAT pueden correr en paralelo; las que excedan esperan turno.
    # Cada una levanta Chromium (Playwright), así que súbelo según la RAM del web.
    SUNAT_MAX_CONCURRENCY: int = 2
    # Igual que SUNAT pero para SIRE: cuántas conciliaciones corren a la vez en el
    # proceso web (on-demand). Cada una espera a SUNAT (~min, sin conexión a la BD)
    # y corre el motor en un subproceso efímero.
    SIRE_MAX_CONCURRENCY: int = 2
    # Igual para el Scanner: cuántas extracciones OCR corren a la vez en el web
    # (on-demand). El OCR es CPU-bound; ajústalo a los núcleos/RAM del web.
    SCANNER_MAX_CONCURRENCY: int = 2
    # Descarga automatizada (módulo SUNAT / Playwright).
    DESCARGAS_DIR: str = ""  # vacío → carpeta temporal del SO
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    DRIVE_REDIRECT_URI: str = "http://localhost:8000/api/sunat/drive/callback"
    # Ejecutable de Tesseract OCR (módulo Scanner). En PATH por defecto.
    SCANNER_TESSERACT_CMD: str = "tesseract"

    # ─── CORS (orígenes del frontend permitidos) ───
    # Lista separada por comas (nunca "*" en producción). Se usa texto plano en
    # vez de JSON para que la variable de entorno sea simple y robusta:
    #   CORS_ORIGINS=https://app.vercel.app,https://otro.com
    CORS_ORIGINS: str = "http://localhost:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origen.strip() for origen in self.CORS_ORIGINS.split(",") if origen.strip()]

    @model_validator(mode="after")
    def _exigir_encryption_key_en_produccion(self) -> "Settings":
        """En producción no se permite arrancar con la llave de cifrado de dev."""
        if self.ENV == "production" and self.ENCRYPTION_KEY in (
            "",
            _DEFAULT_DEV_ENCRYPTION_KEY,
        ):
            raise ValueError(
                "ENCRYPTION_KEY debe definirse por variable de entorno en producción "
                "(no uses el valor por defecto de desarrollo)."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Instancia única (cacheada) de la configuración."""
    return Settings()


settings = get_settings()
