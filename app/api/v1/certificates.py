\
"""Endpoints para enfileirar consulta de CA e captura do PDF."""

import logging
import re
from typing import Any

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.task_history import register_queued
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


def _enqueue(task_function, normalized: str) -> TaskAccepted:
    task = task_function.delay(normalized)
    # O worker pode começar antes desta escrita; register_queued não regride o estado.
    register_queued(task.id, task_function.name, normalized)
    logger.info(
        "certificate_task_enqueued",
        extra={
            "event": "task_enqueued", "task_name": task_function.name,
            "task_id": task.id, "ca_number": normalized,
        },
    )
    return TaskAccepted(task_id=task.id, ca_number=normalized)


@router.post("/{ca_number}/refresh", response_model=TaskAccepted, status_code=202)
def enqueue_refresh(ca_number: str) -> TaskAccepted:
    return _enqueue(refresh_source, _validate_ca_number(ca_number))


@router.post("/{ca_number}/pdf", response_model=TaskAccepted, status_code=202)
def enqueue_pdf(ca_number: str) -> TaskAccepted:
    return _enqueue(fetch_certificate_pdf, _validate_ca_number(ca_number))


@router.get("/tasks/{task_id}")
def task_status(task_id: str) -> dict[str, Any]:
    result = AsyncResult(task_id, app=celery_app)
    response: dict[str, Any] = {"task_id": task_id, "state": result.state}
    if result.successful():
        response["result"] = result.result
    elif result.failed():
        response["error"] = "task_failed"
    return response
