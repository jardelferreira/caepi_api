"""Endpoints read-only destinados a sistemas integrados via chave de API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import Response
from sqlalchemy import select

from app.api.v1.admin import _serialize_certificate
from app.core.database import SessionLocal
from app.models.ca import Certificate
from app.models.document import CertificateDocument
from app.security.auth import require_certificate_read, require_document_read
from app.storage import get_object_store

router = APIRouter(prefix="/certificates", tags=["integration"])


def _normalize_ca(value: str) -> str:
    normalized = "".join(ch for ch in value if ch.isdigit())
    if not normalized or len(normalized) > 32:
        raise HTTPException(status_code=422, detail="Informe um número de CA válido.")
    return normalized


@router.get("/{ca_number}", summary="Consultar um CA por chave de API", description=(
    "Consulta os dados já armazenados na base local. Não inicia scraping. "
    "Requer Authorization: Bearer <chave> com escopo certificates:read."
))
def get_certificate(ca_number: str = Path(..., min_length=1, max_length=64), _key=Depends(require_certificate_read)) -> dict:
    normalized = _normalize_ca(ca_number)
    with SessionLocal() as db:
        certificate = db.scalar(select(Certificate).where(Certificate.ca_number == normalized))
        if certificate is None:
            raise HTTPException(status_code=404, detail=f"CA {normalized} não encontrado na base local.")
        return {
            "certificate": _serialize_certificate(db, certificate),
            "source": "local_database",
            "scraping_started": False,
        }


@router.get("/{ca_number}/pdf", summary="Baixar PDF de um CA por chave de API", description=(
    "Entrega o PDF já armazenado. Não inicia download ou scraping. "
    "Requer o escopo documents:read."
))
def get_certificate_pdf(ca_number: str = Path(..., min_length=1, max_length=64), _key=Depends(require_document_read)) -> Response:
    normalized = _normalize_ca(ca_number)
    with SessionLocal() as db:
        certificate = db.scalar(select(Certificate).where(Certificate.ca_number == normalized))
        if certificate is None:
            raise HTTPException(status_code=404, detail=f"CA {normalized} não encontrado na base local.")
        document = db.scalar(
            select(CertificateDocument)
            .where(
                CertificateDocument.certificate_id == certificate.id,
                CertificateDocument.document_type == "certificate_pdf",
                CertificateDocument.status == "downloaded",
                CertificateDocument.object_key.is_not(None),
            )
            .order_by(CertificateDocument.created_at.desc())
        )
        if document is None or not document.object_key:
            raise HTTPException(status_code=404, detail=f"PDF do CA {normalized} não está armazenado.")
        object_key = document.object_key
        sha256 = document.sha256 or ""
    try:
        content = get_object_store().get_bytes(object_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Não foi possível recuperar o PDF do armazenamento.") from exc
    headers = {
        "Content-Disposition": f'attachment; filename="CA-{normalized}-certificado.pdf"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    if sha256:
        headers["ETag"] = f'"{sha256}"'
    return Response(content=content, media_type="application/pdf", headers=headers)
