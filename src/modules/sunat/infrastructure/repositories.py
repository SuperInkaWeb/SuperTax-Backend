"""Repositorios SQLAlchemy del módulo SUNAT (schema `sunat`)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.sunat.domain.entities import JobResult
from src.modules.sunat.infrastructure.models import (
    DriveTokenModel,
    JobResultModel,
    SunatCredentialsModel,
)


class SqlSunatCredentialsRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, company_id: int) -> SunatCredentialsModel | None:
        return self._db.scalar(
            select(SunatCredentialsModel).where(
                SunatCredentialsModel.company_id == company_id
            )
        )

    def upsert(
        self,
        company_id: int,
        updated_by_id: int,
        ruc: str,
        usuario_enc: str,
        clave_enc: str,
    ) -> None:
        creds = self.get(company_id)
        if creds is None:
            creds = SunatCredentialsModel(company_id=company_id)
            self._db.add(creds)
        creds.ruc = ruc
        creds.usuario_enc = usuario_enc
        creds.clave_enc = clave_enc
        creds.updated_by_id = updated_by_id
        self._db.commit()


class SqlJobResultRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_by_company(
        self, company_id: int, limit: int, offset: int
    ) -> list[JobResult]:
        rows = self._db.scalars(
            select(JobResultModel)
            .where(JobResultModel.company_id == company_id)
            .order_by(JobResultModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [
            JobResult(id=r.id, job_id=r.job_id, created_at=r.created_at, resultados=r.resultados)
            for r in rows
        ]


class SqlDriveTokenRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, company_id: int) -> DriveTokenModel | None:
        return self._db.scalar(
            select(DriveTokenModel).where(DriveTokenModel.company_id == company_id)
        )

    def upsert(self, company_id: int, access_enc: str, refresh_enc: str) -> None:
        token = self.get(company_id)
        if token is None:
            token = DriveTokenModel(company_id=company_id)
            self._db.add(token)
        token.access_token_enc = access_enc
        token.refresh_token_enc = refresh_enc
        self._db.commit()

    def delete(self, company_id: int) -> None:
        token = self.get(company_id)
        if token is not None:
            self._db.delete(token)
            self._db.commit()
