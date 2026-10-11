"""Testes de integração opcionais com PostgreSQL e SeaweedFS reais.

Execute explicitamente com CAEPI_RUN_INTEGRATION=1. O teste cria um CA sintético
com número exclusivo e remove somente esse CA e os objetos que criou.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select

pytestmark = pytest.mark.integration

if os.getenv("CAEPI_RUN_INTEGRATION") != "1":
    pytest.skip(
        "Integração real desativada. Defina CAEPI_RUN_INTEGRATION=1 para executar.",
        allow_module_level=True,
    )

from app.core.database import SessionLocal  # noqa: E402
from app.models.ca import Certificate  # noqa: E402
from app.models.document import CertificateDocument  # noqa: E402
from app.models.observation import CertificateObservation  # noqa: E402
from app.storage import get_object_store  # noqa: E402
import app.workers.tasks as tasks  # noqa: E402


class IntegrationScraper:
    """Substitui somente o portal externo; banco e armazenamento são reais."""

    def __init__(self, ca_number: str, pdf_bytes: bytes):
        self.ca_number = ca_number
        self.pdf_bytes = pdf_bytes

    def fetch_certificate(self, ca_number: str):
        return SimpleNamespace(
            ca_number=ca_number,
            fields={"situacao": "TESTE INTEGRACAO", "equipamento": "EPI sintético"},
            collected_at=datetime.now(timezone.utc),
            warnings=[],
        )

    def fetch_pdf(self, ca_number: str):
        return SimpleNamespace(
            content=self.pdf_bytes,
            content_type="application/pdf",
            source_url=f"https://integration.invalid/ca/{ca_number}.pdf",
        )


def _valid_test_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def integration_case(monkeypatch):
    """Cria e limpa apenas os dados sintéticos deste teste."""
    ca_number = str(time.time_ns())[-12:]
    pdf_bytes = _valid_test_pdf()
    store = get_object_store()
    created_keys: set[str] = set()

    def scraper_factory():
        return IntegrationScraper(ca_number, pdf_bytes)

    monkeypatch.setattr("app.scrapers.caepi_portal.CaepiPortalScraper", scraper_factory)

    try:
        yield ca_number, pdf_bytes, store, created_keys
    finally:
        with SessionLocal() as db:
            certificate = db.scalar(
                select(Certificate).where(Certificate.ca_number == ca_number)
            )
            if certificate is not None:
                docs = db.scalars(
                    select(CertificateDocument).where(
                        CertificateDocument.certificate_id == certificate.id
                    )
                ).all()
                created_keys.update(
                    doc.object_key for doc in docs if doc.object_key
                )
                db.delete(certificate)
                db.commit()
        for key in created_keys:
            try:
                store.delete_object(key)
            except Exception as exc:  # cleanup errors must remain visible
                pytest.fail(f"Falha ao remover objeto de teste {key}: {exc}")


def test_postgres_and_seaweedfs_pdf_idempotency(integration_case):
    ca_number, pdf_bytes, store, created_keys = integration_case

    first = tasks.fetch_certificate_pdf.run(ca_number)
    assert first["status"] == "downloaded"
    assert first["certificate_id"] > 0
    assert first["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    created_keys.add(first["document_key"])

    stored_bytes = store.get_bytes(first["document_key"])
    assert stored_bytes == pdf_bytes
    assert hashlib.sha256(stored_bytes).hexdigest() == first["sha256"]

    second = tasks.fetch_certificate_pdf.run(ca_number)
    assert second["status"] == "unchanged"
    assert second["deduplicated_by"] == "sha256"
    assert second["document_key"] == first["document_key"]

    with SessionLocal() as db:
        certificate = db.scalar(
            select(Certificate).where(Certificate.ca_number == ca_number)
        )
        assert certificate is not None
        document_count = db.scalar(
            select(func.count())
            .select_from(CertificateDocument)
            .where(CertificateDocument.certificate_id == certificate.id)
        )
        observation_count = db.scalar(
            select(func.count())
            .select_from(CertificateObservation)
            .where(CertificateObservation.certificate_id == certificate.id)
        )
        assert document_count == 1
        assert observation_count == 2
