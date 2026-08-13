"""
Dominio del módulo SUNAT (puro, sin frameworks).

Descarga automatizada de comprobantes desde SUNAT (Playwright). Aquí solo vive el
resultado persistido de un job; la orquestación del navegador es infraestructura.
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass
class JobResult:
    id: int
    job_id: str
    created_at: datetime
    resultados: str  # JSON con el detalle de la descarga
