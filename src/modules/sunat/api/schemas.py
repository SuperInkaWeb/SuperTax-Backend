"""Schemas Pydantic del módulo SUNAT."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CredentialsInput(BaseModel):
    ruc: str = Field(min_length=11, max_length=11)
    usuario: str = Field(min_length=1, max_length=50)
    clave: str = Field(min_length=1)


class CredentialsStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    configured: bool
    ruc: str | None = None


class JobResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: str
    created_at: datetime


class DriveStatusResponse(BaseModel):
    connected: bool
