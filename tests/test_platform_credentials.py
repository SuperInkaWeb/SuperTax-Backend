"""
Bóveda de credenciales de plataforma: fuente única + fallback legacy.

Verifican la lógica aislada (cifrado, actualización parcial, delegación de SUNAT
y SIRE a la bóveda, y el fallback de lectura al almacén legacy). No tocan SUNAT ni
SIRE reales: la autenticación en vivo no es verificable en tests.
"""
import pytest

from src.modules.sire.application import credentials as sire_creds
from src.modules.sire.infrastructure.reconciliation.orchestrator import (
    _cargar_credenciales,
)
from src.modules.sunat.application import credentials as sunat_creds
from src.modules.sunat.infrastructure.models import SunatCredentialsModel
from src.platform.credentials import vault
from src.platform.security import decrypt_field, encrypt_field
from src.platform.tenancy.models import Company
from src.platform.users.models import User


@pytest.fixture
def empresa_y_usuario(db_session):
    company = Company(ruc="20123456789", razon_social="Estudio Credenciales")
    user = User(email="cred@estudio.pe", nombre="Cred", auth0_sub="auth0|cred")
    db_session.add_all([company, user])
    db_session.commit()
    return company, user


def test_vault_cifra_y_conserva_secreto_al_actualizar_identificador(
    db_session, empresa_y_usuario
):
    company, user = empresa_y_usuario
    vault.write(db_session, company.id, vault.KIND_SOL, "USUARIO1", "clave-secreta", user.id)

    guardada = vault.get(db_session, company.id, vault.KIND_SOL)
    assert guardada is not None
    assert guardada.identifier == "USUARIO1"
    assert guardada.secret == "clave-secreta"
    assert guardada.secret_enc != "clave-secreta"  # nunca en claro

    # Secreto vacío = conservar; solo cambia el identificador.
    vault.write(db_session, company.id, vault.KIND_SOL, "USUARIO2", "", user.id)
    actualizada = vault.get(db_session, company.id, vault.KIND_SOL)
    assert actualizada.identifier == "USUARIO2"
    assert actualizada.secret == "clave-secreta"


def test_vault_exige_secreto_la_primera_vez(db_session, empresa_y_usuario):
    company, user = empresa_y_usuario
    with pytest.raises(ValueError):
        vault.write(db_session, company.id, vault.KIND_SOL, "USUARIO", "", user.id)


def test_sunat_login_usa_ruc_de_empresa_desde_la_boveda(db_session, empresa_y_usuario):
    company, user = empresa_y_usuario
    sunat_creds.set_credentials(
        db_session, company.id, user.id, ruc="IGNORADO", usuario="SOLUSER", clave="sol123"
    )

    login = sunat_creds.get_saved_login(db_session, company.id)
    assert login == ("20123456789", "SOLUSER", "sol123")  # ruc viene de la empresa

    estado = sunat_creds.get_credentials_status(db_session, company.id)
    assert estado.configured is True
    assert estado.ruc == "20123456789"
    assert estado.usuario == "SOLUSER"


def test_sunat_login_cae_al_almacen_legacy(db_session, empresa_y_usuario):
    company, user = empresa_y_usuario
    # Sin fila en la bóveda: solo existe la credencial legacy del módulo.
    db_session.add(
        SunatCredentialsModel(
            company_id=company.id,
            ruc="20123456789",
            usuario_enc=encrypt_field("LEGACYUSER"),
            clave_enc=encrypt_field("legacy-clave"),
            updated_by_id=user.id,
        )
    )
    db_session.commit()

    assert vault.get(db_session, company.id, vault.KIND_SOL) is None
    login = sunat_creds.get_saved_login(db_session, company.id)
    assert login == ("20123456789", "LEGACYUSER", "legacy-clave")


def test_sire_guarda_ambas_clases_y_el_loader_descifra(db_session, empresa_y_usuario):
    company, user = empresa_y_usuario
    sire_creds.set_credentials(
        db_session,
        company.id,
        user.id,
        usuario_sol="SOLSIRE",
        clave_sol="clave-sol",
        client_id="client-abc",
        client_secret="secret-xyz",
    )

    estado = sire_creds.get_credentials_status(db_session, company.id)
    assert estado.configured is True
    assert estado.usuario_sol == "SOLSIRE"
    assert estado.client_id == "client-abc"

    # El loader del orquestador entrega los blobs cifrados que la auth descifra.
    fuente = _cargar_credenciales(db_session, company.id)
    assert fuente.usuario_sol == "SOLSIRE"
    assert fuente.client_id == "client-abc"
    assert decrypt_field(fuente.clave_sol_enc) == "clave-sol"
    assert decrypt_field(fuente.client_secret_enc) == "secret-xyz"


def test_sire_secreto_vacio_sin_previo_es_error(db_session, empresa_y_usuario):
    company, user = empresa_y_usuario
    with pytest.raises(ValueError):
        sire_creds.set_credentials(
            db_session, company.id, user.id,
            usuario_sol="X", clave_sol="", client_id="c", client_secret="s",
        )
