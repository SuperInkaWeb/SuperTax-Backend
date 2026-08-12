"""Base declarativa única para todos los modelos (núcleo Core + módulos)."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
