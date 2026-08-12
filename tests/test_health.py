"""La plataforma arranca y responde el liveness (sin depender de infraestructura)."""


def test_health_liveness(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "Plataforma"
