"""Testes de resiliência sem chamadas reais ao portal nem ao armazenamento."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

import app.workers.tasks as tasks
from app.models.ca import Certificate
from app.models.document import CertificateDocument
from test_tasks_idempotency import test_environment


class PortalUnavailable:
    def fetch_certificate(self, ca_number):
        raise TimeoutError("falha simulada do portal CAEPI")

    def fetch_pdf(self, ca_number):
        raise AssertionError("não deveria buscar PDF se a consulta falhou")


class StorageUnavailable:
    def put_bytes(self, **kwargs):
        raise ConnectionError("falha simulada do SeaweedFS")

    def get_bytes(self, key):
        raise AssertionError("nenhum objeto anterior esperado")


def test_refresh_portal_failure_does_not_create_or_change_records(test_environment, monkeypatch):
    session_factory, _, _ = test_environment
    monkeypatch.setattr(
        "app.scrapers.caepi_portal.CaepiPortalScraper",
        lambda: PortalUnavailable(),
    )

    with pytest.raises(TimeoutError, match="falha simulada"):
        tasks.refresh_source.run("42049")

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Certificate)) == 0
        assert db.scalar(select(func.count()).select_from(CertificateDocument)) == 0


def test_pdf_portal_failure_does_not_create_document(test_environment, monkeypatch):
    session_factory, store, _ = test_environment
    monkeypatch.setattr(
        "app.scrapers.caepi_portal.CaepiPortalScraper",
        lambda: PortalUnavailable(),
    )

    with pytest.raises(TimeoutError, match="falha simulada"):
        tasks.fetch_certificate_pdf.run("42049")

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Certificate)) == 0
        assert db.scalar(select(func.count()).select_from(CertificateDocument)) == 0
    assert store.objects == {}


def test_storage_failure_does_not_mark_pdf_as_downloaded(test_environment, monkeypatch):
    session_factory, _, _ = test_environment

    scraped = SimpleNamespace(
        ca_number="42049",
        fields={"situacao": "VÁLIDO", "equipamento": "Respirador de teste"},
        collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        warnings=[],
    )
    pdf = SimpleNamespace(
        content=b"%PDF-1.4 simulated",
        content_type="application/pdf",
        source_url="https://example.test/42049.pdf",
    )

    class Scraper:
        def fetch_certificate(self, ca_number):
            return scraped

        def fetch_pdf(self, ca_number):
            return pdf

    monkeypatch.setattr("app.scrapers.caepi_portal.CaepiPortalScraper", lambda: Scraper())
    monkeypatch.setattr(tasks, "get_object_store", lambda: StorageUnavailable())

    with pytest.raises(ConnectionError, match="falha simulada"):
        tasks.fetch_certificate_pdf.run("42049")

    with session_factory() as db:
        # A consulta do certificado foi persistida, mas o PDF não pode constar
        # como baixado se o armazenamento falhou antes do commit do documento.
        assert db.scalar(select(func.count()).select_from(Certificate)) == 1
        assert db.scalar(select(func.count()).select_from(CertificateDocument)) == 0
