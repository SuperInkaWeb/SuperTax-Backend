"""Vinculación OAuth de buzón (Fase 2): state cifrado y validaciones. Sin red ni BD."""
import urllib.parse

import pytest

from src.modules.correos.application import mailbox_oauth
from src.platform.config.settings import settings


@pytest.fixture()
def google_configurado(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "secret")


def _state_de_url(url: str) -> str:
    query = urllib.parse.urlparse(url).query
    return urllib.parse.parse_qs(query)["state"][0]


def test_autorizar_construye_url_y_state(google_configurado):
    url = mailbox_oauth.autorizar(company_id=7, user_id=3, provider="gmail")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "code_challenge_method=S256" in url
    assert "gmail.readonly" in url
    # El state descifra a la misma empresa/usuario/proveedor y trae el code_verifier.
    data = mailbox_oauth._leer_state(_state_de_url(url), company_id=7, user_id=3)
    assert data["c"] == 7 and data["u"] == 3 and data["p"] == "gmail" and data["v"]


def test_state_de_otra_sesion_rechazado(google_configurado):
    url = mailbox_oauth.autorizar(company_id=7, user_id=3, provider="gmail")
    with pytest.raises(mailbox_oauth.OAuthError):
        mailbox_oauth._leer_state(_state_de_url(url), company_id=99, user_id=3)


def test_proveedor_no_configurado_falla():
    # Sin MS_CLIENT_ID/SECRET (vacíos por defecto en local) → error 503 claro.
    with pytest.raises(mailbox_oauth.OAuthError):
        mailbox_oauth.autorizar(company_id=1, user_id=1, provider="outlook")


def test_rutas_oauth_montadas():
    from src.main import create_app

    paths = create_app().openapi()["paths"]
    assert "/api/correos/mailboxes/authorize" in paths
    assert "/api/correos/mailboxes/callback" in paths
    assert "/api/correos/mailboxes/{connection_id}" in paths
