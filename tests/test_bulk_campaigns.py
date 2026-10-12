
from app.api.v1 import certificates as certificates_api
from app.main import app
from conftest import make_admin_client

client = make_admin_client(app)


def test_campaign_preview_is_read_only_and_reports_eligible_count(monkeypatch):
    monkeypatch.setattr(certificates_api, "_campaign_candidates", lambda scope, stale_days: ["10001", "10002"])
    monkeypatch.setattr(certificates_api, "find_active_campaign", lambda: None)

    response = client.get("/api/v1/certificates/campaigns/preview?scope=unqueried&stale_days=90")

    assert response.status_code == 200
    payload = response.json()
    assert payload["eligible_count"] == 2
    assert payload["sample_ca_numbers"] == ["10001", "10002"]
    assert payload["automatic_execution"] is False


def test_campaign_start_requires_explicit_confirmation(monkeypatch):
    monkeypatch.setattr(
        certificates_api, "_campaign_candidates",
        lambda scope, stale_days: (_ for _ in ()).throw(AssertionError("must not query before confirm")),
    )
    response = client.post(
        "/api/v1/certificates/campaigns",
        json={"scope": "unqueried", "operation": "refresh", "max_cas": 100},
    )
    assert response.status_code == 400
    assert "Confirme explicitamente" in response.json()["detail"]


def test_campaign_preview_count_must_match_before_start(monkeypatch):
    monkeypatch.setattr(certificates_api, "_campaign_candidates", lambda scope, stale_days: ["10001", "10002"])
    monkeypatch.setattr(
        certificates_api, "create_bulk_campaign",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not start with stale preview")),
    )
    response = client.post(
        "/api/v1/certificates/campaigns",
        json={
            "scope": "unqueried", "operation": "refresh", "max_cas": 100,
            "expected_eligible_count": 3, "confirmed": True,
        },
    )
    assert response.status_code == 409
    assert "quantidade elegível mudou" in response.json()["detail"]


def test_campaign_start_snapshots_selected_count_and_operation(monkeypatch):
    monkeypatch.setattr(certificates_api, "_campaign_candidates", lambda scope, stale_days: ["10001", "10002", "10003"])
    captured = {}

    def fake_create(numbers, **kwargs):
        captured["numbers"] = list(numbers)
        captured.update(kwargs)
        return {"campaign_id": "abcdef12-0000-4000-8000-000000000000", "state": "RUNNING", "total": len(numbers)}

    monkeypatch.setattr(certificates_api, "create_bulk_campaign", fake_create)
    response = client.post(
        "/api/v1/certificates/campaigns",
        json={
            "scope": "unqueried", "operation": "refresh", "max_cas": 2,
            "expected_eligible_count": 3, "confirmed": True,
        },
    )

    assert response.status_code == 202
    assert captured["numbers"] == ["10001", "10002"]
    assert captured["operation"] == "refresh"
    assert response.json()["eligible_count"] == 3
    assert response.json()["selected_count"] == 2


def test_campaign_create_rejects_zero_and_invalid_scopes():
    zero = client.post(
        "/api/v1/certificates/campaigns",
        json={"scope": "all", "operation": "refresh", "max_cas": 0, "confirmed": True},
    )
    bad_scope = client.get("/api/v1/certificates/campaigns/preview?scope=automatic")
    assert zero.status_code == 422
    assert bad_scope.status_code == 422


def test_campaign_report_csv_exports_terminal_results(monkeypatch):
    monkeypatch.setattr(
        certificates_api, "get_bulk_campaign",
        lambda campaign_id: {"campaign_id": campaign_id, "state": "COMPLETED", "total": 1},
    )
    monkeypatch.setattr(
        certificates_api, "list_campaign_report_tasks",
        lambda campaign_id, limit=50000: [{
            "ca_number": "15272", "operation": "refresh", "state": "SUCCESS",
            "result_status": "ok", "task_id": "task-15272", "duration_ms": "14000",
        }],
    )

    response = client.get("/api/v1/certificates/campaigns/abcdef12-0000-4000-8000-000000000000/report.csv")

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment; filename=\"caepi-campaign-abcdef12.csv\"" in response.headers["content-disposition"]
    assert "CA;Operação;Estado;Resultado" in response.text
    assert "15272;refresh;SUCCESS;ok" in response.text
