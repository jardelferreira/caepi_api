from __future__ import annotations

import hashlib
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.api_key import ApiKey
from app.models.ca import Certificate
from app.models.document import CertificateDocument
from app.main import app
from app.api.v1 import admin as admin_api
from app.api.v1 import integrations as integration_api
from app.security import auth as auth_security
from conftest import make_admin_client


@pytest.fixture

def isolated_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(admin_api, "SessionLocal", testing_session)
    monkeypatch.setattr(integration_api, "SessionLocal", testing_session)
    monkeypatch.setattr(auth_security, "SessionLocal", testing_session)
    try:
        with testing_session() as db:
            db.add(Certificate(
                ca_number="15272",
                status="VÁLIDO",
                valid_until=date(2030, 1, 31),
                manufacturer="Fabricante de teste",
                equipment_name="Óculos de proteção",
                brand="Marca teste",
                consolidated_data={"referencia": "TESTE-1", "_meta": {"sources": {"caepi_portal": {"collected_at": "2026-10-12T10:00:00+00:00"}}}},
                last_source="caepi_portal",
            ))
            db.commit()
        yield testing_session
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_admin_pages_redirect_to_login_when_unauthenticated():
    client = TestClient(app, follow_redirects=False)
    response = client.get("/admin/api-keys")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_rejects_bad_credentials():
    client = TestClient(app)
    response = client.post("/api/v1/admin/auth/login", json={"username": "wrong", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Usuário ou senha incorretos."


def test_create_list_and_revoke_api_key(isolated_db):
    client = make_admin_client(app)
    create = client.post("/api/v1/admin/api-keys", json={
        "name": "ERP de teste",
        "scopes": ["certificates:read"],
        "expires_in_days": 90,
    })
    assert create.status_code == 201, create.text
    payload = create.json()
    secret = payload["api_key"]
    assert secret.startswith("caepi_live_")
    assert "api_key" not in client.get("/api/v1/admin/api-keys").json()["items"][0]

    # Hashes são persistidos, nunca o segredo original.
    with isolated_db() as db:
        stored = db.scalar(select(ApiKey).where(ApiKey.id == payload["id"]))
        assert stored is not None
        assert stored.key_hash == hashlib.sha256(secret.encode()).hexdigest()
        assert stored.key_hash != secret

    revoke = client.delete(f"/api/v1/admin/api-keys/{payload['id']}")
    assert revoke.status_code == 200
    listed = client.get("/api/v1/admin/api-keys").json()
    assert listed["items"][0]["active"] is False


def test_external_read_requires_key_and_does_not_start_scraping(isolated_db, monkeypatch):
    client = make_admin_client(app)
    create = client.post("/api/v1/admin/api-keys", json={
        "name": "Consumidor leitura",
        "scopes": ["certificates:read"],
        "expires_in_days": None,
    })
    assert create.status_code == 201, create.text
    token = create.json()["api_key"]

    unauthorized = TestClient(app).get("/api/v1/certificates/15272")
    assert unauthorized.status_code == 401

    response = TestClient(app).get(
        "/api/v1/certificates/15272",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["certificate"]["ca_number"] == "15272"
    assert payload["certificate"]["manufacturer"] == "Fabricante de teste"
    assert payload["source"] == "local_database"
    assert payload["scraping_started"] is False

    not_found = TestClient(app).get(
        "/api/v1/certificates/99999999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert not_found.status_code == 404


def test_key_scope_limits_pdf_download(isolated_db):
    client = make_admin_client(app)
    create = client.post("/api/v1/admin/api-keys", json={
        "name": "Somente leitura CA",
        "scopes": ["certificates:read"],
        "expires_in_days": None,
    })
    token = create.json()["api_key"]
    response = TestClient(app).get(
        "/api/v1/certificates/15272/pdf",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "documents:read" in response.json()["detail"]
