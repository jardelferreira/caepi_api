from types import SimpleNamespace


from app.api.v1 import certificates as certificates_api
from app.main import app
from conftest import make_admin_client

client = make_admin_client(app)


class FakeScalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)


class FakeSession:
    def __init__(self, *, found=(), catalog=(), statuses=("VÁLIDO", "VENCIDO"), total=0):
        self.found = list(found)
        self.catalog = list(catalog)
        self.statuses = list(statuses)
        self.total = total

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def scalar(self, _statement):
        return self.total

    def scalars(self, statement):
        # Tests exercise route behavior, not SQL rendering. Catalog selects Certificate;
        # batch validation selects ca_number. Return configured fixtures for each query.
        first = statement.column_descriptions[0] if statement.column_descriptions else {}
        expr = first.get("expr")
        if expr is certificates_api.Certificate:
            return FakeScalars(self.catalog)
        if expr is certificates_api.Certificate.status:
            return FakeScalars(self.statuses)
        return FakeScalars(self.found)


def test_batch_refresh_queues_unique_cas_only(monkeypatch):
    queued = []
    monkeypatch.setattr(
        certificates_api,
        "SessionLocal",
        lambda: FakeSession(found=["15272", "42049"]),
    )

    def fake_delay(ca_number):
        queued.append(ca_number)
        return SimpleNamespace(id=f"task-{ca_number}")

    monkeypatch.setattr(certificates_api.refresh_source, "delay", fake_delay)
    registrations = []
    monkeypatch.setattr(
        certificates_api, "register_queued",
        lambda *_args, **kwargs: registrations.append(kwargs.get("batch_id")),
    )

    response = client.post(
        "/api/v1/certificates/batch",
        json={"operation": "refresh", "ca_numbers": ["CA 15272", "15272", "42049"]},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["requested"] == 2
    assert payload["queued"] == 2
    assert [item["ca_number"] for item in payload["items"]] == ["15272", "42049"]
    assert queued == ["15272", "42049"]
    assert registrations == [payload["batch_id"], payload["batch_id"]]


def test_batch_pdf_queues_pdf_tasks(monkeypatch):
    queued = []
    monkeypatch.setattr(
        certificates_api,
        "SessionLocal",
        lambda: FakeSession(found=["15272"]),
    )

    def fake_delay(ca_number):
        queued.append(ca_number)
        return SimpleNamespace(id="pdf-task-1")

    monkeypatch.setattr(certificates_api.fetch_certificate_pdf, "delay", fake_delay)
    monkeypatch.setattr(certificates_api, "register_queued", lambda *_args, **_kwargs: None)

    response = client.post(
        "/api/v1/certificates/batch",
        json={"operation": "pdf", "ca_numbers": ["15272"]},
    )

    assert response.status_code == 202
    assert response.json()["items"][0]["task_id"] == "pdf-task-1"
    assert response.json()["items"][0]["operation"] == "pdf"
    assert queued == ["15272"]


def test_batch_rejects_more_than_ten_unique_cas(monkeypatch):
    monkeypatch.setattr(
        certificates_api,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be queried")),
    )
    response = client.post(
        "/api/v1/certificates/batch",
        json={"operation": "refresh", "ca_numbers": [str(n) for n in range(100, 111)]},
    )
    assert response.status_code == 422
    assert "limite por lote é de 10" in response.json()["detail"]


def test_batch_rejects_cas_not_in_imported_database(monkeypatch):
    monkeypatch.setattr(
        certificates_api,
        "SessionLocal",
        lambda: FakeSession(found=["15272"]),
    )
    response = client.post(
        "/api/v1/certificates/batch",
        json={"operation": "refresh", "ca_numbers": ["15272", "999999"]},
    )
    assert response.status_code == 422
    assert "999999" in response.json()["detail"]


def test_catalog_returns_paginated_ca_records(monkeypatch):
    row = SimpleNamespace(
        ca_number="15272",
        status="VÁLIDO",
        valid_until=None,
        manufacturer="Fabricante Exemplo",
        equipment_name="Capacete",
        brand="Exemplo",
        last_source="mte_official_base",
        last_source_update=None,
        consolidated_data={"official_base": {"NRRegistroCA": "15272"}},
    )
    monkeypatch.setattr(
        certificates_api,
        "SessionLocal",
        lambda: FakeSession(catalog=[row], total=1),
    )

    response = client.get("/api/v1/certificates/catalog?limit=20&offset=0&unqueried_only=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["ca_number"] == "15272"
    assert payload["items"][0]["has_official_base"] is True


def test_catalog_non_numeric_search_returns_no_rows(monkeypatch):
    monkeypatch.setattr(
        certificates_api,
        "SessionLocal",
        lambda: FakeSession(catalog=[], total=0),
    )
    response = client.get("/api/v1/certificates/catalog?q=abc")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0



def test_catalog_supports_text_search_and_all_filter_dimensions(monkeypatch):
    row = SimpleNamespace(
        ca_number="15272",
        status="VÁLIDO",
        valid_until=None,
        manufacturer="3M do Brasil",
        equipment_name="Respirador semifacial",
        brand="3M",
        last_source="caepi_portal",
        last_source_update=None,
        consolidated_data={"official_base": {"NRRegistroCA": "15272"}},
    )
    monkeypatch.setattr(
        certificates_api,
        "SessionLocal",
        lambda: FakeSession(catalog=[row], statuses=["VÁLIDO", "VENCIDO"], total=1),
    )

    response = client.get(
        "/api/v1/certificates/catalog",
        params={
            "q": "3M",
            "status_filter": "VÁLIDO",
            "validity_filter": "undated",
            "source_filter": "portal",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "3M"
    assert payload["status_filter"] == "VÁLIDO"
    assert payload["validity_filter"] == "undated"
    assert payload["source_filter"] == "portal"
    assert payload["status_options"] == ["VENCIDO", "VÁLIDO"]
    assert payload["items"][0]["brand"] == "3M"
    assert payload["items"][0]["valid_until"] is None


def test_legacy_unqueried_only_parameter_is_supported(monkeypatch):
    monkeypatch.setattr(
        certificates_api,
        "SessionLocal",
        lambda: FakeSession(catalog=[], total=0),
    )
    response = client.get(
        "/api/v1/certificates/catalog",
        params={"unqueried_only": "false", "source_filter": "portal"},
    )
    assert response.status_code == 200
    # For compatibility, the legacy explicit boolean wins over source_filter.
    assert response.json()["source_filter"] == "all"



def test_dedicated_certificate_page_has_navigation_and_filter_controls():
    response = client.get("/certificates")
    assert response.status_code == 200
    assert 'href="/dashboard"' in response.text
    assert 'href="/certificates"' in response.text
    assert 'id="batch-query"' in response.text
    assert 'id="batch-status-filter"' in response.text
    assert 'id="batch-validity-filter"' in response.text
    assert 'id="batch-source-filter"' in response.text


def test_integrity_summary_detects_status_validity_conflict_divergence_and_staleness(monkeypatch):
    from datetime import date, datetime, timedelta, timezone

    expired_official = date.today() - timedelta(days=2)
    portal_valid_until = date.today() + timedelta(days=180)
    row = SimpleNamespace(
        ca_number="10048",
        status="VÁLIDO",
        valid_until=expired_official,
        manufacturer="Fabricante Portal",
        equipment_name="Calçado tipo botina",
        brand="Marca Portal",
        last_source="mte_official_base",
        last_source_update=datetime.now(timezone.utc),
        consolidated_data={
            "official_base": {
                "NRRegistroCA": "10048",
                "Situacao": "VÁLIDO",
                "DataValidade": expired_official.strftime("%d/%m/%Y"),
                "RazaoSocial": "Fabricante Oficial",
                "NomeEquipamento": "Calçado tipo botina",
                "MarcaCA": "Marca Oficial",
            },
            "situacao": "VÁLIDO",
            "data_validade": portal_valid_until.strftime("%d/%m/%Y"),
            "razao_social": "Fabricante Portal",
            "equipamento": "Calçado tipo botina",
            "marca": "Marca Portal",
        },
    )
    monkeypatch.setattr(
        certificates_api, "SessionLocal", lambda: FakeSession(catalog=[row], total=1)
    )

    response = client.get("/api/v1/certificates/integrity/summary?stale_days=90")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_certificates"] == 1
    assert payload["status_validity_conflict"] == 1
    assert payload["official_portal_divergence"] == 1
    assert payload["portal_stale"] == 1
    assert payload["any_issue"] == 1


def test_catalog_integrity_filter_returns_only_matching_cas(monkeypatch):
    from datetime import date, datetime, timedelta, timezone

    conflict = SimpleNamespace(
        ca_number="10048",
        status="VÁLIDO",
        valid_until=date.today() - timedelta(days=1),
        manufacturer="Fabricante",
        equipment_name="Botina",
        brand="Marca",
        last_source="caepi_portal",
        last_source_update=datetime.now(timezone.utc),
        consolidated_data={"situacao": "VÁLIDO", "data_validade": "10/10/2026"},
    )
    clean = SimpleNamespace(
        ca_number="10049",
        status="VENCIDO",
        valid_until=date.today() - timedelta(days=1),
        manufacturer="Fabricante",
        equipment_name="Capacete",
        brand="Marca",
        last_source="caepi_portal",
        last_source_update=datetime.now(timezone.utc),
        consolidated_data={"situacao": "VENCIDO", "data_validade": "10/10/2020"},
    )
    monkeypatch.setattr(
        certificates_api,
        "SessionLocal",
        lambda: FakeSession(catalog=[conflict, clean], statuses=["VÁLIDO", "VENCIDO"], total=2),
    )

    response = client.get(
        "/api/v1/certificates/catalog",
        params={
            "source_filter": "all",
            "integrity_filter": "status_validity_conflict",
            "limit": 20,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["ca_number"] for item in payload["items"]] == ["10048"]
    assert "status_validity_conflict" in payload["items"][0]["integrity_issue_codes"]


def test_integrity_summary_endpoint_is_read_only_and_has_stale_days(monkeypatch):
    monkeypatch.setattr(
        certificates_api, "SessionLocal", lambda: FakeSession(catalog=[], total=0)
    )
    response = client.get("/api/v1/certificates/integrity/summary?stale_days=180")
    assert response.status_code == 200
    assert response.json()["stale_days"] == 180
    assert response.json()["total_certificates"] == 0


def test_certificate_page_has_integrity_panel_and_filters():
    response = client.get("/certificates")
    assert response.status_code == 200
    assert 'id="integrity-manager"' in response.text
    assert 'id="batch-integrity-filter"' in response.text
    assert 'id="integrity-stale-days"' in response.text
    assert 'data-integrity-filter="official_portal_divergence"' in response.text
    assert 'id="integrity-count-status"' in response.text
