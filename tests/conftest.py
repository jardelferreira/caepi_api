"""Configuração de teste isolada; os valores abaixo só existem no processo pytest."""
import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-password-not-for-production")
os.environ.setdefault("ADMIN_SESSION_SECRET", "test-session-secret-" + "a" * 48)
os.environ.setdefault("ADMIN_COOKIE_SECURE", "false")


def make_admin_client(app):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.post(
        "/api/v1/admin/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    assert response.status_code == 200, response.text
    session_response = client.get("/api/v1/admin/auth/session")
    assert session_response.status_code == 200, session_response.text
    client.headers.update({"X-CSRF-Token": session_response.json()["csrf_token"]})
    return client
