"""
Tests de soporte (tickets): creación por el cliente, hilo de mensajes, cambio de
estado según quién responde, cierre y aislamiento entre empresas.
"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.platform.authorization.models import Role
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import Company, Membership
from src.platform.users.models import User
from src.seed import seed


def _override(db, user):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db


def _rol(db, key: str) -> Role:
    return db.scalar(select(Role).where(Role.key == key))


def _cliente_con_empresa(db) -> tuple[User, Company]:
    seed(db)
    empresa = Company(ruc="20100000001", razon_social="Cliente Uno SAC")
    user = User(email="cliente@estudio.pe", nombre="Cliente", auth0_sub="auth0|cli")
    db.add_all([empresa, user])
    db.flush()
    db.add(Membership(user_id=user.id, company_id=empresa.id, role_id=_rol(db, "operador").id))
    db.flush()
    return user, empresa


def _crear_ticket(db, user, empresa) -> int:
    _override(db, user)
    try:
        resp = TestClient(app).post(
            "/api/tickets",
            json={"asunto": "No puedo descargar", "mensaje": "Falla la descarga SUNAT"},
            headers={"X-Company-Id": str(empresa.id)},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_cliente_crea_ticket_queda_abierto(db_session):
    user, empresa = _cliente_con_empresa(db_session)
    ticket_id = _crear_ticket(db_session, user, empresa)

    _override(db_session, user)
    try:
        detalle = TestClient(app).get(f"/api/tickets/{ticket_id}")
    finally:
        app.dependency_overrides.clear()
    cuerpo = detalle.json()
    assert cuerpo["status"] == "abierto"
    assert len(cuerpo["mensajes"]) == 1
    assert cuerpo["mensajes"][0]["es_soporte"] is False


def test_soporte_responde_pasa_a_respondido(db_session):
    user, empresa = _cliente_con_empresa(db_session)
    ticket_id = _crear_ticket(db_session, user, empresa)
    soporte = User(email="admin@plataforma.pe", nombre="Soporte", auth0_sub="auth0|sop")
    soporte.is_platform_admin = True
    db_session.add(soporte)
    db_session.flush()

    _override(db_session, soporte)
    try:
        resp = TestClient(app).post(
            f"/api/tickets/{ticket_id}/reply", json={"mensaje": "Revisando tu caso"}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["status"] == "respondido"
    assert cuerpo["mensajes"][-1]["es_soporte"] is True


def test_cliente_responde_vuelve_a_abierto(db_session):
    user, empresa = _cliente_con_empresa(db_session)
    ticket_id = _crear_ticket(db_session, user, empresa)

    _override(db_session, user)
    try:
        resp = TestClient(app).post(
            f"/api/tickets/{ticket_id}/reply", json={"mensaje": "Sigue fallando"}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["status"] == "abierto"


def test_ticket_cerrado_rechaza_respuesta(db_session):
    user, empresa = _cliente_con_empresa(db_session)
    ticket_id = _crear_ticket(db_session, user, empresa)

    _override(db_session, user)
    try:
        client = TestClient(app)
        cerrar = client.post(f"/api/tickets/{ticket_id}/close")
        assert cerrar.status_code == 200
        assert cerrar.json()["status"] == "cerrado"
        rechazo = client.post(f"/api/tickets/{ticket_id}/reply", json={"mensaje": "Hola?"})
    finally:
        app.dependency_overrides.clear()
    assert rechazo.status_code == 400


def test_miembro_de_otra_empresa_no_accede(db_session):
    user, empresa = _cliente_con_empresa(db_session)
    ticket_id = _crear_ticket(db_session, user, empresa)
    intruso = User(email="otro@estudio.pe", nombre="Otro", auth0_sub="auth0|otr")
    otra = Company(ruc="20100000099", razon_social="Ajena SAC")
    db_session.add_all([intruso, otra])
    db_session.flush()
    db_session.add(
        Membership(user_id=intruso.id, company_id=otra.id, role_id=_rol(db_session, "operador").id)
    )
    db_session.flush()

    _override(db_session, intruso)
    try:
        resp = TestClient(app).get(f"/api/tickets/{ticket_id}")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_soporte_lista_todos_los_tickets(db_session):
    user, empresa = _cliente_con_empresa(db_session)
    _crear_ticket(db_session, user, empresa)
    soporte = User(email="admin@plataforma.pe", nombre="Soporte", auth0_sub="auth0|sop")
    soporte.is_platform_admin = True
    db_session.add(soporte)
    db_session.flush()

    _override(db_session, soporte)
    try:
        resp = TestClient(app).get("/api/tickets")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
