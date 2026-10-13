"""
Concurrencia GLOBAL acotada para el trabajo pesado de DuckDB.

Un único semáforo limita cuántas operaciones pesadas corren A LA VEZ —ingesta +
materialización + vistas/descargas de análisis— sin importar cuántos usuarios las
disparen (backpressure: lo que excede espera turno). Así el pico de RAM del contenedor
≈ DATAFILTER_MAX_CONCURRENCY × límite de memoria, y nunca se desborda.

El preview paginado NO pasa por aquí (es liviano y frecuente; serializarlo haría
lento el scroll). Su RAM la devuelve el `malloc_trim` del motor tras cada consulta.
"""
import logging
from contextlib import contextmanager
from threading import BoundedSemaphore

from src.platform.config.settings import settings

_log = logging.getLogger("datafilter.heavy")
# Presupuesto global de operaciones pesadas simultáneas (se fija al importar).
_GATE = BoundedSemaphore(max(1, settings.DATAFILTER_MAX_CONCURRENCY))


@contextmanager
def gate():
    """Toma un cupo del presupuesto global de operaciones pesadas (espera si no hay)."""
    _GATE.acquire()
    try:
        yield
    finally:
        _GATE.release()


def log_rss(contexto: str) -> None:
    """Registra la RSS actual del proceso (Linux /proc) para medir memoria. Best-effort."""
    try:
        with open("/proc/self/status", encoding="ascii") as archivo:
            for linea in archivo:
                if linea.startswith("VmRSS:"):
                    _log.info("RSS tras %s: %s", contexto, linea.split(":", 1)[1].strip())
                    return
    except Exception:
        pass  # no-Linux o sin /proc: se omite
