"""Testes de resiliência e configuração de retentativas Celery."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from celery.exceptions import Retry
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


def _assert_retry_policy(task):
    assert task.max_retries == 3
    assert task.autoretry_for == (Exception,)
    assert task.dont_autoretry_for == (ValueError,)
    assert task.retry_backoff is True
    assert task.retry_backoff_max == 300
    assert task.retry_jitter is True


def test_tasks_have_bounded_retry_policy():
    _assert_retry_policy(tasks.refresh_source)
    _assert_retry_policy(tasks.fetch_certificate_pdf)


def test_refresh_portal_failure_requests_retry_without_creating_records(test_environment, monkeypatch):
    session_factory, _, _ = test_environment
    monkeypatch.setattr("app.scrapers.caepi_portal.CaepiPortalScraper", lambda: PortalUnavailable())

    retry_calls = []

    def request_retry(*args, **kwargs):
        retry_calls.append(kwargs)
        raise Retry("retry requested")

    monkeypatch.setattr(tasks.refresh_source, "retry", request_retry)
    with pytest.raises(Retry, match="retry requested"):
        tasks.refresh_source.run("42049")
    assert len(retry_calls) == 1
    assert isinstance(retry_calls[0].get("exc"), TimeoutError)

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Certificate)) == 0
        assert db.scalar(select(func.count()).select_from(CertificateDocument)) == 0


def test_pdf_portal_failure_requests_retry_without_creating_records(test_environment, monkeypatch):
    session_factory, store, _ = test_environment
    monkeypatch.setattr("app.scrapers.caepi_portal.CaepiPortalScraper", lambda: PortalUnavailable())

    retry_calls = []

    def request_retry(*args, **kwargs):
        retry_calls.append(kwargs)
        raise Retry("retry requested")

    monkeypatch.setattr(tasks.fetch_certificate_pdf, "retry", request_retry)
    with pytest.raises(Retry, match="retry requested"):
        tasks.fetch_certificate_pdf.run("42049")
    assert len(retry_calls) == 1
    assert isinstance(retry_calls[0].get("exc"), TimeoutError)

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Certificate)) == 0
        assert db.scalar(select(func.count()).select_from(CertificateDocument)) == 0
    assert store.objects == {}


def test_storage_failure_requests_retry_and_does_not_mark_pdf_downloaded(test_environment, monkeypatch):
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

    retry_calls = []

    def request_retry(*args, **kwargs):
        retry_calls.append(kwargs)
        raise Retry("retry requested")

    monkeypatch.setattr(tasks.fetch_certificate_pdf, "retry", request_retry)
    with pytest.raises(Retry, match="retry requested"):
        tasks.fetch_certificate_pdf.run("42049")
    assert len(retry_calls) == 1
    assert isinstance(retry_calls[0].get("exc"), ConnectionError)

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Certificate)) == 1
        assert db.scalar(select(func.count()).select_from(CertificateDocument)) == 0
