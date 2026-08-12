"""
Tests del endpoint /me y del guardado de seguridad.

- Sin token → rechazado (no se llega al handler).
- Con usuario resuelto → devuelve sus empresas (Modelo B) para el frontend.
"""
from fastapi.testclient import TestClient

from src.main import app
from src.platform.authorization.models import Role
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import Company, Membership
from src.platform.users.models import User


def test_me_sin_token_es_rechazado(client: TestClient):
    resp = client.get("/me")
    assert resp.status_code in (401, 403)


def test_me_devuelve_empresas_del_usuario(db_session):
    company = Company(ruc="20999888777", razon_social="Estudio Y")
    role = Role(key="consulta", nombre="Consulta")
    user = User(email="ana@estudio.pe", nombre="Ana", auth0_sub="auth0|ana")
    db_session.add_all([company, role, user])
    db_session.flush()
    db_session.add(Membership(user_id=user.id, company_id=company.id, role_id=role.id))
    db_session.flush()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        resp = TestClient(app).get("/me")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "ana@estudio.pe"
    assert len(body["companies"]) == 1
    assert body["companies"][0]["ruc"] == "20999888777"
