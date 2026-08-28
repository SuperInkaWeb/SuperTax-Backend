"""
Ejecutor de trabajos en segundo plano dentro del proceso web (modelo on-demand).

Reemplaza el sondeo constante de los workers: cuando el usuario lanza una
automatización, la API la encola y la despacha aquí mismo, en un pool de hilos
con concurrencia acotada. Si no hay trabajos, no hay actividad contra la base de
datos → Neon puede suspenderse (ahorro de horas de cómputo).

Es genérico y no depende de los módulos (respeta la independencia): recibe un
callable. Cada módulo provee el suyo (que reclama el job y llama a su motor).

Concurrencia: cada pool (`name`) admite hasta `max_workers` trabajos en paralelo;
los que excedan ese tope esperan en la cola interna del pool y arrancan al
liberarse un cupo. Así solo se "encola" bajo carga real.
"""
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

_log = logging.getLogger("platform.tasks")

_runners: dict[str, ThreadPoolExecutor] = {}
_lock = Lock()


def _get_runner(name: str, max_workers: int) -> ThreadPoolExecutor:
    """Devuelve (creando una sola vez) el pool con nombre `name`."""
    with _lock:
        runner = _runners.get(name)
        if runner is None:
            runner = ThreadPoolExecutor(
                max_workers=max(1, max_workers), thread_name_prefix=f"job-{name}"
            )
            _runners[name] = runner
        return runner


def submit(name: str, max_workers: int, fn: Callable[..., object], *args: object) -> None:
    """Despacha `fn(*args)` en el pool `name`. Un fallo del trabajo no propaga:
    se registra (el estado final del job lo maneja el propio trabajo)."""

    def _guardado() -> None:
        try:
            fn(*args)
        except Exception:
            _log.exception("Trabajo en segundo plano '%s' falló", name)

    _get_runner(name, max_workers).submit(_guardado)
