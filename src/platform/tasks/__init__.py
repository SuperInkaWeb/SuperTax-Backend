"""Ejecución de trabajos en segundo plano dentro del proceso web (on-demand)."""
from src.platform.tasks.executor import submit

__all__ = ["submit"]
