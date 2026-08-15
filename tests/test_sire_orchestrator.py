"""
Tests de la orquestación SIRE (cierre de SIRE) con SUNAT simulado (mock).

No tocan SUNAT real; verifican la LÓGICA de decisión que sí es testeable:
- ticket nuevo vs retoma de un ticket fresco (reanudar),
- compras "sin SIRE": detección de meses + descarga de propuestas extra.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import src.modules.sire.infrastructure.reconciliation.orchestrator as orch
from src.modules.sire.domain.entities import JobStatus, TipoLibro
from src.modules.sire.infrastructure.models import (
    ReconciliationJobModel,
    SireCredentialsModel,
)
from src.modules.sire.infrastructure.sunat import compras as sunat_compras
from src.platform.tenancy.models import Company
from src.platform.users.models import User


def _resultado_falso() -> dict:
    return {
        "escenario_a_count": 0,
        "escenario_b_count": 0,
        "escenario_c_count": 0,
        "escenario_d_count": 0,
        "igv_diferencia_total": 0.0,
        "tiene_alertas_rojas": False,
        "filename_xlsx": "r.xlsx",
        "path_xlsx": "reportes/r.xlsx",
        "excel_size": 10,
        "path_csv_a": None,
        "csv_a_size": None,
        "path_csv": None,
        "csv_b_size": None,
        "path_csv_c": None,
        "csv_c_size": None,
        "path_csv_d": None,
        "csv_d_size": None,
    }


def _empresa_con_creds(db) -> tuple[Company, User]:
    empresa = Company(ruc="20100000077", razon_social="Estudio Orq")
    user = User(email="orq@estudio.pe", nombre="Orq", auth0_sub="auth0|orq")
    db.add_all([empresa, user])
    db.flush()
    db.add(
        SireCredentialsModel(
            company_id=empresa.id,
            usuario_sol="USR",
            clave_sol_enc="x",
            client_id="cid",
            client_secret_enc="y",
        )
    )
    db.flush()
    return empresa, user


def _job(db, empresa, user, status=JobStatus.en_cola, **extra) -> ReconciliationJobModel:
    job = ReconciliationJobModel(
        company_id=empresa.id,
        created_by_id=user.id,
        periodo="202601",
        tipo_libro=TipoLibro.compras,
        status=status,
        empresa_file_path="sire/uploads/x/1/e.csv",
        **extra,
    )
    db.add(job)
    db.flush()
    return job


def _mock_sunat(monkeypatch, contador):
    async def token(*_a, **_k):
        return "tok"

    async def solicitar(_get_token, periodo):
        contador["solicitar"] += 1
        return f"T-{periodo}"

    async def consultar(_get_token, _num_ticket, _periodo):
        contador["consultar"] += 1
        return ("Terminado", None)

    async def descargar(_get_token, _num_ticket, periodo):
        return f"/tmp/no-existe-{periodo}.txt"

    # En producción el motor corre en un subproceso efímero (aísla memoria); en
    # los tests se ejecuta in-process para que los mocks de arriba sean visibles.
    async def _inline(func, payload):
        return func(payload)

    monkeypatch.setattr(orch, "get_sunat_token", token)
    monkeypatch.setattr(sunat_compras, "solicitar_export_compras", solicitar)
    monkeypatch.setattr(sunat_compras, "consultar_ticket_compras", consultar)
    monkeypatch.setattr(sunat_compras, "descargar_ticket_compras", descargar)
    monkeypatch.setattr(orch, "_en_subproceso", _inline)
    monkeypatch.setattr(orch, "procesar_conciliacion", lambda _payload: _resultado_falso())


def test_es_fresco():
    ahora = datetime.now(timezone.utc)
    assert orch._es_fresco(ahora) is True
    assert orch._es_fresco(ahora - timedelta(hours=25)) is False
    assert orch._es_fresco(None) is False


def test_job_nuevo_solicita_ticket_y_completa(db_session, monkeypatch):
    empresa, user = _empresa_con_creds(db_session)
    job = _job(db_session, empresa, user)  # sin num_ticket → debe solicitar
    contador = {"solicitar": 0, "consultar": 0}
    _mock_sunat(monkeypatch, contador)

    asyncio.run(orch.procesar_job(db_session, job.id))

    db_session.refresh(job)
    assert job.status == JobStatus.completado
    assert contador["solicitar"] == 1
    assert job.num_ticket == "T-202601"


def test_reanudar_retoma_ticket_fresco_sin_resolicitar(db_session, monkeypatch):
    empresa, user = _empresa_con_creds(db_session)
    job = _job(
        db_session,
        empresa,
        user,
        status=JobStatus.error,
        num_ticket="T-PREVIO",
        propuesta_origen_at=datetime.now(timezone.utc),  # fresco
    )
    contador = {"solicitar": 0, "consultar": 0}
    _mock_sunat(monkeypatch, contador)

    asyncio.run(orch.procesar_job(db_session, job.id))

    db_session.refresh(job)
    assert job.status == JobStatus.completado
    assert contador["consultar"] == 1  # consultó el ticket previo
    assert contador["solicitar"] == 0  # NO pidió uno nuevo: lo retomó
    assert job.num_ticket == "T-PREVIO"


def test_reutilizar_propuesta_de_otro_job(db_session, monkeypatch):
    empresa, user = _empresa_con_creds(db_session)
    # Job A ya generó una propuesta fresca del mismo periodo/libro.
    _job(db_session, empresa, user, status=JobStatus.completado, num_ticket="T-OTRO")
    # Job B pide reutilizarla.
    job_b = _job(db_session, empresa, user, reutilizar_propuesta=True)
    contador = {"solicitar": 0, "consultar": 0}
    _mock_sunat(monkeypatch, contador)

    asyncio.run(orch.procesar_job(db_session, job_b.id))

    db_session.refresh(job_b)
    assert job_b.status == JobStatus.completado
    assert job_b.num_ticket == "T-OTRO"  # reutilizó la propuesta del otro job
    assert contador["solicitar"] == 0  # no pidió una nueva


def test_propuesta_disponible(db_session, monkeypatch):
    empresa, user = _empresa_con_creds(db_session)
    contador = {"solicitar": 0, "consultar": 0}
    _mock_sunat(monkeypatch, contador)

    # Sin candidato → no disponible.
    vacio = asyncio.run(
        orch.consultar_propuesta_disponible(db_session, empresa.id, "202601", TipoLibro.compras)
    )
    assert vacio["disponible"] is False

    # Con un job que tiene ticket fresco → disponible (consultar responde Terminado).
    _job(db_session, empresa, user, status=JobStatus.completado, num_ticket="T-X")
    resp = asyncio.run(
        orch.consultar_propuesta_disponible(db_session, empresa.id, "202601", TipoLibro.compras)
    )
    assert resp["disponible"] is True


def test_sin_sire_descarga_meses_extra_y_persiste_tickets(db_session, monkeypatch):
    empresa, user = _empresa_con_creds(db_session)
    job = _job(db_session, empresa, user, sin_sire=True)
    contador = {"solicitar": 0, "consultar": 0}
    _mock_sunat(monkeypatch, contador)
    monkeypatch.setattr(orch, "extraer_periodos_emision", lambda _p: ["202511", "202512"])

    payload_capturado: dict = {}

    def capturar(payload):
        payload_capturado.update(payload)
        return _resultado_falso()

    monkeypatch.setattr(orch, "procesar_conciliacion", capturar)

    asyncio.run(orch.procesar_job(db_session, job.id))

    db_session.refresh(job)
    assert job.status == JobStatus.completado
    # Se descargaron y persistieron las propuestas de los meses rezagados.
    assert set(job.extra_tickets.keys()) == {"202511", "202512"}
    assert set(payload_capturado["sunat_extra_paths"].keys()) == {"202511", "202512"}
