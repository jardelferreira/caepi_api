"""Teste opcional de retentativas usando um worker Celery real e fila isolada.

Executar somente com CAEPI_RUN_WORKER_RETRY=1. Usa o Redis configurado no projeto,
mas consome exclusivamente a fila caepi-retry-validation, sem tocar na fila padrão.
"""
import os
import uuid

import pytest
from celery.contrib.testing.worker import start_worker

from app.workers.celery_app import celery_app

pytestmark = pytest.mark.skipif(
    os.getenv("CAEPI_RUN_WORKER_RETRY") != "1",
    reason="Defina CAEPI_RUN_WORKER_RETRY=1 para iniciar worker de teste isolado",
)

QUEUE = "caepi-retry-validation"


def _register_probe(name, *, fail_times, always_fail=False):
    attempts = {"count": 0}
    task_name = f"tests.retry_probe.{name}.{uuid.uuid4().hex}"

    @celery_app.task(
        bind=True,
        name=task_name,
        max_retries=2,
        default_retry_delay=0,
        acks_late=True,
    )
    def probe(self):
        attempts["count"] += 1
        if always_fail or attempts["count"] <= fail_times:
            if self.request.retries >= self.max_retries:
                raise RuntimeError("falha permanente simulada")
            raise self.retry(
                exc=RuntimeError("falha transitória simulada"),
                countdown=0,
                queue=QUEUE,
            )
        return {"status": "ok", "attempts": attempts["count"]}

    return probe, attempts


def test_real_worker_recovers_after_transient_failure():
    task, attempts = _register_probe("transient", fail_times=1)

    with start_worker(
        celery_app,
        queues=[QUEUE],
        perform_ping_check=False,
        pool="solo",
        concurrency=1,
        loglevel="WARNING",
    ):
        result = task.apply_async(queue=QUEUE)
        value = result.get(timeout=20, propagate=True)

    assert value == {"status": "ok", "attempts": 2}
    assert attempts["count"] == 2
    assert result.state == "SUCCESS"


def test_real_worker_stops_after_retry_limit():
    task, attempts = _register_probe("permanent", fail_times=0, always_fail=True)

    with start_worker(
        celery_app,
        queues=[QUEUE],
        perform_ping_check=False,
        pool="solo",
        concurrency=1,
        loglevel="WARNING",
    ):
        result = task.apply_async(queue=QUEUE)
        with pytest.raises(RuntimeError, match="falha permanente simulada"):
            result.get(timeout=20, propagate=True)

    assert attempts["count"] == 3  # execução inicial + 2 retentativas
    assert result.state == "FAILURE"
