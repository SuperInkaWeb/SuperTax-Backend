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
    # Al arrancar, aplicar migraciones + seed idempotentes (auto-reparación si el
    # pre-deploy del proveedor no corre). Protegido con un advisory lock de Postgres
    # para que solo UNA instancia lo haga. Ponlo en false si prefieres migrar solo
    # en un paso de despliegue dedicado.
    DB_BOOTSTRAP_ON_STARTUP: bool = True

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
    # DataFilter: cuántas operaciones PESADAS de DuckDB (ingestas + materializaciones
    # + vistas de análisis) corren en PARALELO, cada una en un subproceso efímero. El
    # resto espera turno (backpressure), así subir/analizar muchos nunca desborda la
    # RAM. Pico ≈ DATAFILTER_MAX_CONCURRENCY × DUCKDB_MEMORY_LIMIT; súbelo si tu plan
    # tiene RAM de sobra (más throughput, más pico).
    DATAFILTER_MAX_CONCURRENCY: int = 2
    # Tope de filas que una descarga filtrada puede generar (defensa A04 / costo).
    DATAFILTER_MAX_DOWNLOAD_ROWS: int = 5_000_000
    # Tamaño máximo de archivo a subir (MB). Defensa contra abuso / costo (A04).
    DATAFILTER_MAX_UPLOAD_MB: int = 2048
    # Segundos máximos por consulta DuckDB antes de cortarla (0 = sin tope). Evita que
    # una consulta patológica retenga RAM/CPU indefinidamente (defensa A04 / costo).
    DATAFILTER_QUERY_TIMEOUT_SECONDS: int = 120
    # Guardas de memoria de DuckDB (anti-OOM en contenedor, ve la RAM del host):
    # límite de RAM por conexión en los SUBPROCESOS pesados (vacío = default de DuckDB)
    # y carpeta de volcado a disco al superarlo (vacío → temporal del SO).
    DUCKDB_MEMORY_LIMIT: str = ""
    DUCKDB_TEMP_DIR: str = ""
    # Límite de RAM de DuckDB en el PROCESO WEB (preview/valores, consultas livianas y
    # frecuentes). Se mantiene bajo para que el web —always-on— no acumule memoria.
    # Vacío = usa DUCKDB_MEMORY_LIMIT (o el default de DuckDB si también está vacío).
    DUCKDB_WEB_MEMORY_LIMIT: str = ""
    # Correos: cuántos escaneos (descarga de adjuntos + OCR/IA) corren a la vez en el
    # web (on-demand). Cada uno es I/O (buzón) + CPU (OCR en subproceso); el resto espera.
    CORREOS_MAX_CONCURRENCY: int = 2
    # OAuth de buzón. Google reutiliza GOOGLE_CLIENT_ID/SECRET (mismo cliente que Drive,
    # con el scope gmail.readonly). Outlook usa un cliente de Microsoft propio.
    MS_CLIENT_ID: str = ""
    MS_CLIENT_SECRET: str = ""
    MS_TENANT: str = "common"
    # URL del frontend que recibe el callback OAuth (misma página para ambos proveedores;
    # debe estar registrada como redirect en las consolas de Google y Microsoft).
    CORREOS_OAUTH_REDIRECT_URI: str = "http://localhost:5173/correos/oauth/callback"
    # Validez del `state` cifrado (anti-CSRF/replay): la vuelta del consentimiento.
    CORREOS_OAUTH_STATE_TTL_SECONDS: int = 600
    # Tamaño máximo por adjunto descargado (MB). Más grande se rechaza (A04/costo).
    CORREOS_MAX_ATTACHMENT_MB: int = 25
    # Extracción: la IA de visión (Groq) es el último motor, de pago. Si se apaga, el
    # pipeline usa solo los motores gratuitos (texto nativo/tablas/OCR local).
    CORREOS_IA_HABILITADA: bool = True
    # Tope de llamadas a la IA por escaneo (control de costo; el resto degrada a OCR).
    CORREOS_MAX_IA_POR_ESCANEO: int = 50
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
