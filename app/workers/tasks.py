"""Tarefas Celery para coleta de certificados CAEPI."""

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from pypdf import PdfReader

from app.core.database import SessionLocal
from app.models.ca import Certificate
from app.models.document import CertificateDocument
from app.models.observation import CertificateObservation
from app.storage import get_object_store
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.health_check")
def health_check() -> dict[str, str]:
    return {"status": "ok", "component": "celery-worker"}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    raw = str(value).strip().split(" ")[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _save_certificate(scraped) -> int:
    """Mescla dados novos sem apagar valores existentes por campos vazios.

    Também registra observações por campo na tabela já existente, sem alterar o schema.
    """
    fields = scraped.fields
    mapped_values = {
        "status": fields.get("situacao"),
        "valid_until": _parse_date(fields.get("data_validade")),
        "manufacturer_document": fields.get("cnpj"),
        "manufacturer": fields.get("razao_social"),
        "equipment_name": fields.get("equipamento"),
        "equipment_description": fields.get("descricao"),
        "brand": fields.get("marca"),
        "protection_details": fields.get("laudo") or fields.get("obs_laudo"),
    }

    with SessionLocal() as db:
        certificate = db.scalar(
            select(Certificate).where(Certificate.ca_number == scraped.ca_number)
        )
        if certificate is None:
            certificate = Certificate(ca_number=scraped.ca_number)
            db.add(certificate)
            db.flush()

        for attribute, new_value in mapped_values.items():
            if new_value is None or (isinstance(new_value, str) and not new_value.strip()):
                continue
            old_value = getattr(certificate, attribute)
            if old_value != new_value:
                setattr(certificate, attribute, new_value)

        previous_data = dict(certificate.consolidated_data or {})
        merged_data = {**previous_data}
        changed_fields: list[tuple[str, Any]] = []
        for key, value in fields.items():
            if value is None or value == "" or value == [] or value == {}:
                continue
            if merged_data.get(key) != value:
                changed_fields.append((key, value))
            merged_data[key] = value
        certificate.consolidated_data = merged_data
        certificate.last_source = "caepi_portal"
        certificate.last_source_update = scraped.collected_at
        db.flush()

        for field_name, value in changed_fields:
            serialized = value if isinstance(value, str) else json.dumps(
                value, ensure_ascii=False, sort_keys=True, default=str
            )
            db.add(CertificateObservation(
                certificate_id=certificate.id,
                source="caepi_portal",
                field_name=field_name[:100],
                value_text=serialized,
                raw_payload={"value": value},
                collected_at=scraped.collected_at,
            ))

        db.commit()
        db.refresh(certificate)
        return certificate.id


@celery_app.task(
    name="app.workers.tasks.refresh_source",
    autoretry_for=(Exception,),
    dont_autoretry_for=(ValueError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def refresh_source(ca_number: str | None = None) -> dict[str, Any]:
    """Consulta um CA específico no portal oficial; sem número, não faz varredura ampla."""
    if not ca_number or not str(ca_number).strip():
        raise ValueError("Informe ca_number. A consulta em massa não está habilitada.")
    from app.scrapers.caepi_portal import CaepiPortalScraper

    scraper = CaepiPortalScraper()
    scraped = scraper.fetch_certificate(str(ca_number))
    if scraped is None:
        return {"status": "not_found", "ca_number": str(ca_number)}
    certificate_id = _save_certificate(scraped)
    return {
        "status": "ok",
        "ca_number": scraped.ca_number,
        "certificate_id": certificate_id,
        "fields_extracted": len(scraped.fields),
        "warnings": scraped.warnings,
    }



def _extract_pdf_text(content: bytes) -> str:
    """Extrai texto do PDF e normaliza espaços para comparar versões documentais."""
    from io import BytesIO

    try:
        reader = PdfReader(BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""
    return " ".join(text.split())




@celery_app.task(
    name="app.workers.tasks.fetch_certificate_pdf",
    autoretry_for=(Exception,),
    dont_autoretry_for=(ValueError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def fetch_certificate_pdf(ca_number: str) -> dict[str, Any]:
    normalized = re.sub(r"\D", "", str(ca_number or ""))
    if not normalized or len(normalized) > 32:
        raise ValueError("Informe um número de CA válido.")

    from app.scrapers.caepi_portal import CaepiPortalScraper

    scraper = CaepiPortalScraper()
    # Primeiro garante que o certificado existe/está atualizado no banco.
    scraped = scraper.fetch_certificate(normalized)
    if scraped is None:
        return {"status": "not_found", "ca_number": normalized}
    certificate_id = _save_certificate(scraped)
    pdf = scraper.fetch_pdf(normalized)
    sha256 = hashlib.sha256(pdf.content).hexdigest()
    store = get_object_store()
    checked_at = datetime.now(timezone.utc)

    # Primeiro caminho: hash idêntico já registrado. Evita regravar o mesmo objeto.
    with SessionLocal() as db:
        exact_match = db.scalar(
            select(CertificateDocument).where(
                CertificateDocument.certificate_id == certificate_id,
                CertificateDocument.document_type == "certificate_pdf",
                CertificateDocument.sha256 == sha256,
            )
        )
        if exact_match is not None:
            exact_match.status = "downloaded"
            exact_match.source_url = pdf.source_url
            exact_match.last_checked_at = checked_at
            existing_key = exact_match.object_key
            existing_size = exact_match.size_bytes or len(pdf.content)
            db.commit()
            return {
                "status": "unchanged",
                "ca_number": normalized,
                "certificate_id": certificate_id,
                "document_key": existing_key,
                "sha256": sha256,
                "size_bytes": existing_size,
                "deduplicated_by": "sha256",
            }

        # Recupera referências dos documentos anteriores. O acesso ao object store
        # é feito fora da sessão para não manter uma transação aberta durante I/O.
        existing_documents = db.scalars(
            select(CertificateDocument).where(
                CertificateDocument.certificate_id == certificate_id,
                CertificateDocument.document_type == "certificate_pdf",
                CertificateDocument.status == "downloaded",
                CertificateDocument.object_key.is_not(None),
            ).order_by(CertificateDocument.id.desc())
        ).all()
        previous = [
            {
                "id": document.id,
                "object_key": document.object_key,
                "sha256": document.sha256,
                "size_bytes": document.size_bytes,
            }
            for document in existing_documents
        ]

    new_text = _extract_pdf_text(pdf.content)
    if new_text:
        for document in previous:
            try:
                old_content = store.get_bytes(document["object_key"])
                old_text = _extract_pdf_text(old_content)
            except Exception:
                # Um objeto antigo indisponível não deve impedir um novo download.
                continue
            if old_text and old_text == new_text:
                with SessionLocal() as db:
                    existing = db.get(CertificateDocument, document["id"])
                    if existing is not None:
                        existing.status = "downloaded"
                        existing.source_url = pdf.source_url
                        existing.last_checked_at = checked_at
                        db.commit()
                return {
                    "status": "unchanged",
                    "ca_number": normalized,
                    "certificate_id": certificate_id,
                    "document_key": document["object_key"],
                    "sha256": document["sha256"],
                    "size_bytes": document["size_bytes"],
                    "deduplicated_by": "extracted_text",
                }

    object_key = f"certificates/{normalized}/certificate-{sha256[:16]}.pdf"
    store.put_bytes(
        key=object_key,
        content=pdf.content,
        content_type=pdf.content_type,
        metadata={"ca-number": normalized, "sha256": sha256},
    )

    with SessionLocal() as db:
        document = db.scalar(
            select(CertificateDocument).where(
                CertificateDocument.certificate_id == certificate_id,
                CertificateDocument.document_type == "certificate_pdf",
                CertificateDocument.sha256 == sha256,
            )
        )
        if document is None:
            document = CertificateDocument(
                certificate_id=certificate_id,
                document_type="certificate_pdf",
            )
            db.add(document)
        document.status = "downloaded"
        document.object_key = object_key
        document.sha256 = sha256
        document.content_type = pdf.content_type
        document.size_bytes = len(pdf.content)
        document.source_url = pdf.source_url
        document.last_checked_at = checked_at
        db.commit()

    return {
        "status": "downloaded",
        "ca_number": normalized,
        "certificate_id": certificate_id,
        "document_key": object_key,
        "sha256": sha256,
        "size_bytes": len(pdf.content),
        "deduplicated_by": None,
    }
