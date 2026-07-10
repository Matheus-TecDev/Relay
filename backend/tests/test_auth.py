from fastapi.testclient import TestClient

from app.core.auth import create_access_token
from app.main import app


def test_login_returns_access_token() -> None:
    client = TestClient(app)

    response = client.post("/api/auth/login", json={"username": "admin", "password": "relay_admin"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"]
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] > 0


def test_login_rejects_invalid_credentials() -> None:
    client = TestClient(app)

    response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})

    assert response.status_code == 401


def test_me_returns_current_user() -> None:
    client = TestClient(app)
    token = create_access_token("admin")

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"username": "admin"}


def test_protected_route_requires_token() -> None:
    client = TestClient(app)

    response = client.get("/api/events")

    assert response.status_code == 401


def test_protected_route_rejects_malformed_token() -> None:
    client = TestClient(app)

    response = client.get("/api/events", headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401
