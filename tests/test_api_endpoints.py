from types import SimpleNamespace

import pytest

from app.api.v1 import certificates as certificates_api
from app.main import app
from conftest import make_admin_client


client = make_admin_client(app)


def test_refresh_endpoint_normalizes_ca_and_queues_task(monkeypatch):
    queued = {}

    def fake_delay(ca_number):
        queued["ca_number"] = ca_number
        return SimpleNamespace(id="refresh-task-123")

    monkeypatch.setattr(certificates_api.refresh_source, "delay", fake_delay)

    response = client.post("/api/v1/certificates/CA%2042.049/refresh")

    assert response.status_code == 202
    assert response.json() == {
        "task_id": "refresh-task-123",
        "status": "queued",
        "ca_number": "42049",
    }
    assert queued["ca_number"] == "42049"


def test_pdf_endpoint_queues_task(monkeypatch):
    queued = {}

    def fake_delay(ca_number):
        queued["ca_number"] = ca_number
        return SimpleNamespace(id="pdf-task-456")

    monkeypatch.setattr(certificates_api.fetch_certificate_pdf, "delay", fake_delay)

    response = client.post("/api/v1/certificates/42049/pdf")

    assert response.status_code == 202
    assert response.json() == {
        "task_id": "pdf-task-456",
        "status": "queued",
        "ca_number": "42049",
    }
    assert queued["ca_number"] == "42049"


@pytest.mark.parametrize("ca_number", ["abc", "---", "1" * 33])
def test_endpoints_reject_invalid_ca_number(ca_number):
    refresh_response = client.post(f"/api/v1/certificates/{ca_number}/refresh")
    pdf_response = client.post(f"/api/v1/certificates/{ca_number}/pdf")

    assert refresh_response.status_code == 422
    assert pdf_response.status_code == 422
    assert refresh_response.json()["detail"] == "Informe um número de CA válido."
    assert pdf_response.json()["detail"] == "Informe um número de CA válido."


def test_task_status_pending_returns_state_only(monkeypatch):
    class FakeAsyncResult:
        state = "PENDING"

        def __init__(self, task_id, app):
            assert task_id == "task-pending"

        def successful(self):
            return False

        def failed(self):
            return False

    monkeypatch.setattr(certificates_api, "AsyncResult", FakeAsyncResult)

    response = client.get("/api/v1/certificates/tasks/task-pending")

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-pending", "state": "PENDING"}


def test_task_status_success_includes_result(monkeypatch):
    class FakeAsyncResult:
        state = "SUCCESS"
        result = {"status": "downloaded", "ca_number": "42049"}

        def __init__(self, task_id, app):
            assert task_id == "task-success"

        def successful(self):
            return True

        def failed(self):
            return False

    monkeypatch.setattr(certificates_api, "AsyncResult", FakeAsyncResult)

    response = client.get("/api/v1/certificates/tasks/task-success")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-success",
        "state": "SUCCESS",
        "result": {"status": "downloaded", "ca_number": "42049"},
    }


def test_task_status_failure_does_not_expose_internal_error(monkeypatch):
    class FakeAsyncResult:
        state = "FAILURE"
        result = RuntimeError("internal database credentials leaked")

        def __init__(self, task_id, app):
            assert task_id == "task-failure"

        def successful(self):
            return False

        def failed(self):
            return True

    monkeypatch.setattr(certificates_api, "AsyncResult", FakeAsyncResult)

    response = client.get("/api/v1/certificates/tasks/task-failure")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-failure",
        "state": "FAILURE",
        "error": "task_failed",
    }
    assert "credentials" not in response.text
