from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.ca import Certificate
from app.models.document import CertificateDocument
from app.models.observation import CertificateObservation
import app.workers.tasks as tasks


class FakeObjectStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def put_bytes(self, key: str, content: bytes, content_type: str, metadata=None) -> None:
        self.objects[key] = content


class FakeScraper:
    def __init__(self, pdf_content: bytes, fields=None):
        self.pdf_content = pdf_content
        self.fields = fields or {
            "situacao": "VÁLIDO",
            "equipamento": "Respirador de teste",
        }

    def fetch_certificate(self, ca_number: str):
        return SimpleNamespace(
            ca_number=ca_number,
            fields=self.fields,
            collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            warnings=[],
        )

    def fetch_pdf(self, ca_number: str):
        return SimpleNamespace(
            content=self.pdf_content,
            content_type="application/pdf",
            source_url=f"https://example.test/ca/{ca_number}.pdf",
        )


@pytest.fixture

def test_environment(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = FakeObjectStore()

    monkeypatch.setattr(tasks, "SessionLocal", TestSession)
    monkeypatch.setattr(tasks, "get_object_store", lambda: store)

    # O teste valida a lógica de comparação, não a extração real de texto do PDF.
    text_by_content: dict[bytes, str] = {}
    monkeypatch.setattr(
        tasks,
        "_extract_pdf_text",
        lambda content: " ".join(text_by_content.get(content, "").split()),
    )

    yield TestSession, store, text_by_content
    Base.metadata.drop_all(engine)
    engine.dispose()


def install_scraper(monkeypatch, pdf_content: bytes, fields=None):
    monkeypatch.setattr(
        "app.scrapers.caepi_portal.CaepiPortalScraper",
        lambda: FakeScraper(pdf_content, fields),
    )


def document_rows(session_factory):
    with session_factory() as db:
        return db.scalars(
            select(CertificateDocument).order_by(CertificateDocument.id)
        ).all()


def test_saving_same_scrape_does_not_duplicate_observations(test_environment):
    session_factory, _, _ = test_environment
    scraped = SimpleNamespace(
        ca_number="42049",
        fields={"situacao": "VÁLIDO", "equipamento": "Respirador"},
        collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    first_id = tasks._save_certificate(scraped)
    second_id = tasks._save_certificate(scraped)

    assert first_id == second_id
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Certificate)) == 1
        assert db.scalar(select(func.count()).select_from(CertificateObservation)) == 2


def test_first_pdf_download_stores_one_document(test_environment, monkeypatch):
    session_factory, store, text_by_content = test_environment
    content = b"pdf-version-one"
    text_by_content[content] = "Certificado CA 42049 versao um"
    install_scraper(monkeypatch, content)

    result = tasks.fetch_certificate_pdf.run("CA 42.049")

    assert result["status"] == "downloaded"
    assert result["deduplicated_by"] is None
    assert result["document_key"] in store.objects
    assert len(document_rows(session_factory)) == 1


def test_repeated_identical_pdf_is_deduplicated_by_hash(test_environment, monkeypatch):
    session_factory, store, text_by_content = test_environment
    content = b"same-pdf-bytes"
    text_by_content[content] = "Mesmo certificado"
    install_scraper(monkeypatch, content)

    first = tasks.fetch_certificate_pdf.run("42049")
    second = tasks.fetch_certificate_pdf.run("42049")

    assert first["status"] == "downloaded"
    assert second["status"] == "unchanged"
    assert second["deduplicated_by"] == "sha256"
    assert second["document_key"] == first["document_key"]
    assert len(document_rows(session_factory)) == 1
    assert len(store.objects) == 1


def test_pdf_with_same_text_but_different_bytes_is_deduplicated(test_environment, monkeypatch):
    session_factory, store, text_by_content = test_environment
    first_bytes = b"pdf-with-metadata-date-one"
    second_bytes = b"pdf-with-metadata-date-two"
    text_by_content[first_bytes] = "CA 42049 - Respirador - Valido"
    text_by_content[second_bytes] = "  CA 42049 -\nRespirador - Valido  "

    install_scraper(monkeypatch, first_bytes)
    first = tasks.fetch_certificate_pdf.run("42049")
    install_scraper(monkeypatch, second_bytes)
    second = tasks.fetch_certificate_pdf.run("42049")

    assert first["status"] == "downloaded"
    assert second["status"] == "unchanged"
    assert second["deduplicated_by"] == "extracted_text"
    assert second["document_key"] == first["document_key"]
    assert len(document_rows(session_factory)) == 1
    assert len(store.objects) == 1


def test_changed_pdf_text_creates_new_version_and_preserves_previous(test_environment, monkeypatch):
    session_factory, store, text_by_content = test_environment
    old_bytes = b"pdf-old-content"
    new_bytes = b"pdf-new-content"
    text_by_content[old_bytes] = "CA 42049 - Respirador modelo antigo"
    text_by_content[new_bytes] = "CA 42049 - Respirador modelo atualizado"

    install_scraper(monkeypatch, old_bytes)
    old_result = tasks.fetch_certificate_pdf.run("42049")
    install_scraper(monkeypatch, new_bytes)
    new_result = tasks.fetch_certificate_pdf.run("42049")

    assert old_result["status"] == "downloaded"
    assert new_result["status"] == "downloaded"
    assert new_result["document_key"] != old_result["document_key"]
    assert len(document_rows(session_factory)) == 2
    assert old_result["document_key"] in store.objects
    assert new_result["document_key"] in store.objects
