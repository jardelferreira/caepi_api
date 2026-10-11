\
"""Configuração do Celery e logging/persistência do ciclo de vida das tarefas."""

import logging
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from celery import Celery
from celery.signals import task_failure, task_postrun, task_prerun, task_retry

from app.core.config import settings
from app.services.task_history import update_task

logger = logging.getLogger("caepi.tasks")
_task_started_at: dict[str, float] = {}


def _task_context(args: Any, kwargs: Any) -> dict[str, Any]:
    """Extrai contexto operacional mínimo sem registrar payloads completos."""
    ca_number = kwargs.get("ca_number") if isinstance(kwargs, dict) else None
    if ca_number is None and isinstance(args, (tuple, list)) and args:
        ca_number = args[0]
    if ca_number is not None:
        ca_number = str(ca_number)[:32]
    return {"ca_number": ca_number}


celery_app = Celery(
    "caepi",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Recife",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_send_sent_event=True,
    worker_send_task_events=True,
    beat_schedule={},  # Ativar após validar a fonte e a cadência oficial.
)


@task_prerun.connect
def _log_task_started(sender=None, task_id=None, args=None, kwargs=None, **extra):
    task_name = getattr(sender, "name", "unknown")
    if task_id:
        _task_started_at[task_id] = perf_counter()
    context = _task_context(args or (), kwargs or {})
    logger.info(
        "celery_task_started",
        extra={"event": "task_started", "task_name": task_name, "task_id": task_id, **context},
    )
    update_task(task_id, state="STARTED", task_name=task_name, ca_number=context["ca_number"])


@task_postrun.connect
def _log_task_finished(sender=None, task_id=None, state=None, args=None, kwargs=None, retval=None, **extra):
    started = _task_started_at.pop(task_id, None) if task_id else None
    duration_ms = round((perf_counter() - started) * 1000) if started is not None else None
    task_name = getattr(sender, "name", "unknown")
    context = _task_context(args or (), kwargs or {})
    logger.info(
        "celery_task_finished",
        extra={
            "event": "task_finished", "task_name": task_name, "task_id": task_id,
            "task_state": state, "duration_ms": duration_ms, **context,
        },
    )
    update_task(
        task_id, state=state or "UNKNOWN", task_name=task_name,
        ca_number=context["ca_number"], finished_at=datetime.now(timezone.utc),
        duration_ms=duration_ms, result=retval,
    )


@task_retry.connect
def _log_task_retry(sender=None, request=None, reason=None, einfo=None, **extra):
    request = request or getattr(sender, "request", None)
    args = getattr(request, "args", ()) if request else ()
    kwargs = getattr(request, "kwargs", {}) if request else {}
    task_id = getattr(request, "id", None)
    task_name = getattr(sender, "name", "unknown")
    context = _task_context(args, kwargs)
    retries = getattr(request, "retries", 0) if request else 0
    logger.warning(
        "celery_task_retry",
        extra={
            "event": "task_retry", "task_name": task_name, "task_id": task_id,
            "retry_number": retries,
            "reason_type": type(reason).__name__ if reason is not None else None, **context,
        },
    )
    update_task(
        task_id, state="RETRY", task_name=task_name, ca_number=context["ca_number"],
        retries=retries, error_type=type(reason).__name__ if reason is not None else None,
        error_message="Falha temporária; nova tentativa agendada.",
    )


@task_failure.connect
def _log_task_failure(sender=None, task_id=None, exception=None, args=None, kwargs=None, **extra):
    task_name = getattr(sender, "name", "unknown")
    context = _task_context(args or (), kwargs or {})
    logger.error(
        "celery_task_failed",
        extra={
            "event": "task_failed", "task_name": task_name, "task_id": task_id,
            "exception_type": type(exception).__name__ if exception is not None else None, **context,
        },
    )
    update_task(
        task_id, state="FAILURE", task_name=task_name, ca_number=context["ca_number"],
        finished_at=datetime.now(timezone.utc),
        error_type=type(exception).__name__ if exception is not None else "UnknownError",
        error_message="A tarefa falhou. Consulte os logs operacionais para detalhes.",
    )
