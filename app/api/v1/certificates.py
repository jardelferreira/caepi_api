"""Endpoints para enfileirar consulta de CA e captura do PDF."""

import logging
import re
from typing import Any

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.workers.celery_app import celery_app
from app.workers.tasks import fetch_certificate_pdf, refresh_source

logger = logging.getLogger("caepi.api.certificates")
router = APIRouter(prefix="/certificates", tags=["certificates"])


class TaskAccepted(BaseModel):
    task_id: str
    status: str = "queued"
    ca_number: str



def _validate_ca_number(value: str) -> str:
    normalized = re.sub(r"\D", "", value or "")

    if not normalized or len(normalized) > 32:
        raise HTTPException(
            status_code=422,
            detail="Informe um número de CA válido.",
        )

    return normalized



@router.post("/{ca_number}/refresh", response_model=TaskAccepted, status_code=202)
def enqueue_refresh(ca_number: str) -> TaskAccepted:
    normalized = _validate_ca_number(ca_number)
    task = refresh_source.delay(normalized)
    logger.info(
        "certificate_refresh_enqueued",
        extra={"event": "task_enqueued", "task_name": refresh_source.name,
               "task_id": task.id, "ca_number": normalized},
    )
    return TaskAccepted(task_id=task.id, ca_number=normalized)


@router.post("/{ca_number}/pdf", response_model=TaskAccepted, status_code=202)
def enqueue_pdf(ca_number: str) -> TaskAccepted:
    normalized = _validate_ca_number(ca_number)
    task = fetch_certificate_pdf.delay(normalized)
    logger.info(
        "certificate_pdf_enqueued",
        extra={"event": "task_enqueued", "task_name": fetch_certificate_pdf.name,
               "task_id": task.id, "ca_number": normalized},
    )
    return TaskAccepted(task_id=task.id, ca_number=normalized)


@router.get("/tasks/{task_id}")
def task_status(task_id: str) -> dict[str, Any]:
    result = AsyncResult(task_id, app=celery_app)
    response: dict[str, Any] = {"task_id": task_id, "state": result.state}
    if result.successful():
        response["result"] = result.result
    elif result.failed():
        # Evita expor stack traces/segredos do worker na API pública.
        response["error"] = "task_failed"
    return response
