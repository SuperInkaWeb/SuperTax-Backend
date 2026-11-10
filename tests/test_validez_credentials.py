"""Tests de las credenciales de validez (kind validez_api en la bóveda): guardar,
consultar estado y la obligatoriedad del secreto la primera vez."""
import pytest

from src.modules.sire.application.credentials import (
    get_validez_status,
    set_validez_credentials,
)
from src.platform.tenancy.models import Company
from src.platform.users.models import User


def _company_y_user(db) -> tuple[int, int]:
    user = User(email="val@e.pe", nombre="Val", auth0_sub="auth0|val")
    company = Company(ruc="20111111111", razon_social="Empresa Val")
    db.add_all([user, company])
    db.flush()
    return company.id, user.id


def test_set_y_status_de_validez(db_session):
    company_id, user_id = _company_y_user(db_session)
    assert get_validez_status(db_session, company_id).configured is False

    set_validez_credentials(db_session, company_id, user_id, "CID-VAL", "secret-val")

    estado = get_validez_status(db_session, company_id)
    assert estado.configured is True
    assert estado.client_id == "CID-VAL"


def test_secret_obligatorio_la_primera_vez(db_session):
    company_id, user_id = _company_y_user(db_session)
    with pytest.raises(ValueError):
        set_validez_credentials(db_session, company_id, user_id, "CID-VAL", "")


def test_editar_client_id_conservando_secret(db_session):
    company_id, user_id = _company_y_user(db_session)
    set_validez_credentials(db_session, company_id, user_id, "CID-1", "secret-1")
    # Secreto vacío = conservar el guardado; solo cambia el client_id.
    set_validez_credentials(db_session, company_id, user_id, "CID-2", "")
    assert get_validez_status(db_session, company_id).client_id == "CID-2"
