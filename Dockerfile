FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencias de sistema:
# - libpq5: runtime de PostgreSQL para psycopg2.
# - tesseract-ocr + tesseract-ocr-spa: OCR del módulo Scanner (usa lang="spa").
# - p7zip-full: extrae los ZIP particionados (.z01, .z02, …) que SUNAT entrega
#   en las propuestas SIRE grandes (provee los binarios 7z/7za).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 tesseract-ocr tesseract-ocr-spa p7zip-full \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

# Chromium de Playwright + sus librerías de sistema, para el worker de descargas
# SUNAT. --with-deps instala las libs del SO que Chromium necesita.
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000
# Forma shell para expandir $PORT (Railway lo inyecta); 8000 por defecto en local.
# Los servicios worker sobreescriben este comando con:
#   python -m workers.sire_worker | workers.sunat_worker | workers.scanner_worker
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
