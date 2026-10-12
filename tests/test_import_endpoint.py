from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import imports as imports_api

app = FastAPI()
app.include_router(imports_api.router, prefix="/api/v1")
client = TestClient(app)


def test_import_endpoint_is_disabled_without_admin_key(monkeypatch):
    monkeypatch.setattr(imports_api.settings, "import_api_key", None)
    response = client.post(
        "/api/v1/imports/official-base",
        files={"file": ("base.csv", b"NRRegistroCA;Situacao\n498;Valido\n", "text/csv")},
    )
    assert response.status_code == 503
    assert "IMPORT_API_KEY" in response.json()["detail"]


def test_import_endpoint_rejects_wrong_admin_key(monkeypatch):
    monkeypatch.setattr(imports_api.settings, "import_api_key", "a-long-test-key")
    response = client.post(
        "/api/v1/imports/official-base",
        headers={"X-Import-Key": "wrong-key"},
        files={"file": ("base.csv", b"NRRegistroCA;Situacao\n498;Valido\n", "text/csv")},
    )
    assert response.status_code == 403


def test_import_endpoint_passes_dry_run_and_digest(monkeypatch):
    monkeypatch.setattr(imports_api.settings, "import_api_key", "a-long-test-key")
    captured = {}

    def fake_import(file_obj, *, filename, file_sha256, dry_run):
        captured["contents"] = file_obj.read()
        captured["filename"] = filename
        captured["sha256"] = file_sha256
        captured["dry_run"] = dry_run
        return {"status": "validated", "dry_run": dry_run, "rows_read": 1}

    monkeypatch.setattr(imports_api, "import_official_base", fake_import)
    payload = b"NRRegistroCA;Situacao\n498;Valido\n"
    response = client.post(
        "/api/v1/imports/official-base?dry_run=true",
        headers={"X-Import-Key": "a-long-test-key"},
        files={"file": ("base.csv", payload, "text/csv")},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "validated", "dry_run": True, "rows_read": 1}
    assert captured["contents"] == payload
    assert captured["filename"] == "base.csv"
    assert captured["dry_run"] is True
    assert len(captured["sha256"]) == 64


def test_staged_import_requires_simulation_before_write(monkeypatch, tmp_path):
    from app.importers import staging

    monkeypatch.setattr(imports_api.settings, "import_api_key", "a-long-test-key")
    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path)
    payload = b"NRRegistroCA;Situacao\n498;Valido\n"
    import hashlib

    digest = hashlib.sha256(payload).hexdigest()
    report = {"filename": "base.csv", "sha256": digest, "unique_ca_count": 1}
    staged = staging.create_staging(payload, filename="base.csv", report=report)
    response = client.post(
        f"/api/v1/imports/import-staged?staging_token={staged['staging_token']}&dry_run=false",
        headers={"X-Import-Key": "a-long-test-key"},
    )
    assert response.status_code == 422
    assert "simulação" in response.json()["detail"]
