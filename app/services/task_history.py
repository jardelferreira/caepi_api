"""Histórico operacional de tarefas armazenado no Redis da aplicação (sem migration SQL)."""
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import redis

from app.core.config import settings

logger = logging.getLogger("caepi.task_history")
HISTORY_KEY = "caepi:task_history:ordered"
TASK_PREFIX = "caepi:task_history:item:"
RETENTION_SECONDS = 90 * 24 * 60 * 60

_client = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(task_id: str) -> str:
    return f"{TASK_PREFIX}{task_id}"


def _safe_summary(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    allowed = (
        "status", "ca_number", "certificate_id", "fields_extracted",
        "document_key", "sha256", "size_bytes", "deduplicated_by", "warnings",
    )
    summary = {key: result[key] for key in allowed if key in result}
    return json.dumps(summary, ensure_ascii=False, default=str) if summary else None


def _save(record: dict[str, Any]) -> None:
    task_id = record.get("task_id")
    if not task_id:
        return
    key = _key(task_id)
    # Atomic field update; avoid overwriting fields not included in an event.
    values = {k: json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else str(v)
              for k, v in record.items() if v is not None}
    pipe = _client.pipeline()
    pipe.hset(key, mapping=values)
    pipe.expire(key, RETENTION_SECONDS)
    pipe.zadd(HISTORY_KEY, {task_id: float(record.get("_score", time.time()))})
    pipe.expire(HISTORY_KEY, RETENTION_SECONDS)
    pipe.execute()


def register_queued(
    task_id: str,
    task_name: str,
    ca_number: str | None,
    *,
    batch_id: str | None = None,
) -> None:
    """Registra a tarefa e, se aplicável, seu lote para recuperar o painel após reload."""
    try:
        key = _key(task_id)
        existing = _client.hgetall(key)
        record = {
            "task_id": task_id,
            "task_name": task_name,
            "ca_number": ca_number,
            "state": existing.get("state", "PENDING") if existing else "PENDING",
            "created_at": existing.get("created_at", _now()) if existing else _now(),
        }
        effective_batch_id = batch_id or existing.get("batch_id")
        if effective_batch_id:
            record["batch_id"] = effective_batch_id
        # Keep a state already written by a fast worker and attach the batch id even
        # when the worker wrote the result before this registration completes.
        _save(record)
    except redis.RedisError:
        logger.exception("task_history_write_failed", extra={"task_id": task_id, "operation": "register_queued"})


def update_task(
    task_id: str | None,
    *,
    state: str,
    task_name: str | None = None,
    ca_number: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    duration_ms: int | None = None,
    retries: int | None = None,
    result: Any = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    if not task_id:
        return
    try:
        key = _key(task_id)
        current = _client.hgetall(key)
        record: dict[str, Any] = {
            "task_id": task_id,
            "task_name": task_name or current.get("task_name", "unknown"),
            "ca_number": ca_number if ca_number is not None else current.get("ca_number", ""),
            "state": state,
            "updated_at": _now(),
            "_score": float(current.get("_score", time.time())),
        }
        if not current.get("created_at"):
            record["created_at"] = _now()
        elif current.get("created_at"):
            record["created_at"] = current["created_at"]
        if started_at is not None:
            record["started_at"] = started_at.isoformat()
        elif current.get("started_at"):
            record["started_at"] = current["started_at"]
        if finished_at is not None:
            record["finished_at"] = finished_at.isoformat()
        elif current.get("finished_at"):
            record["finished_at"] = current["finished_at"]
        if duration_ms is not None:
            record["duration_ms"] = duration_ms
        elif current.get("duration_ms") is not None:
            record["duration_ms"] = current["duration_ms"]
        if retries is not None:
            record["retries"] = max(int(current.get("retries", 0)), retries)
        elif current.get("retries") is not None:
            record["retries"] = current["retries"]
        if result is not None:
            summary = _safe_summary(result)
            if summary:
                record["result_summary"] = summary
                try:
                    result_status = json.loads(summary).get("status")
                    if result_status:
                        record["result_status"] = str(result_status)[:50]
                except (ValueError, AttributeError):
                    pass
        elif current.get("result_summary"):
            record["result_summary"] = current["result_summary"]
        if error_type:
            record["error_type"] = error_type[:120]
        elif current.get("error_type"):
            record["error_type"] = current["error_type"]
        if error_type == "PortalAccessBlockedError":
            record["error_message"] = (
                "Portal respondeu com desafio Cloudflare; a campanha foi pausada para evitar "
                "novas tentativas automáticas. Use uma fonte oficial alternativa ou aguarde "
                "a liberação de acesso."
            )
        elif error_message:
            record["error_message"] = error_message[:500]
        elif current.get("error_message"):
            record["error_message"] = current["error_message"]
        batch_id = current.get("batch_id")
        _save(record)
        if batch_id:
            # Um bloqueio do portal pausa a campanha para não continuar enviando CAs
            # enquanto a origem está protegida. Não tenta contornar o challenge.
            try:
                if state.upper() == "FAILURE" and error_type == "PortalAccessBlockedError":
                    from app.services.campaigns import pause_campaign
                    pause_campaign(batch_id)
                # Campanhas têm contadores próprios; lotes manuais sem hash de campanha são ignorados.
                from app.services.campaigns import record_task_terminal
                record_task_terminal(batch_id, task_id, state)
            except Exception:
                # Falha de telemetria nunca pode transformar um scraping bem-sucedido em falha.
                logger.exception("campaign_task_history_update_failed", extra={"task_id": task_id})
    except redis.RedisError:
        # Observability must not turn a successful scrape into a failed task.
        logger.exception("task_history_write_failed", extra={"task_id": task_id, "operation": "update_task"})



def list_tasks(*, state: str | None = None, ca_number: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    matching = []
    for task_id in _client.zrevrange(HISTORY_KEY, 0, -1):
        values = _client.hgetall(_key(task_id))
        if not values:
            continue
        if state and values.get("state", "").upper() != state.upper():
            continue
        if ca_number and values.get("ca_number", "") != ca_number:
            continue
        for key in ("duration_ms", "retries"):
            if key in values:
                try:
                    values[key] = int(values[key])
                except ValueError:
                    pass
        if values.get("result_summary"):
            try:
                values["result_summary"] = json.loads(values["result_summary"])
            except ValueError:
                pass
        matching.append(values)
    return {
        "items": matching[offset:offset + limit],
        "total": len(matching),
        "limit": limit,
        "offset": offset,
    }


def task_summary() -> dict[str, int]:
    counts = {"PENDING": 0, "STARTED": 0, "RETRY": 0, "SUCCESS": 0, "FAILURE": 0}
    for task_id in _client.zrevrange(HISTORY_KEY, 0, -1):
        state = _client.hget(_key(task_id), "state")
        if state in counts:
            counts[state] += 1
    counts["total"] = sum(counts.values())
    counts["active"] = counts["PENDING"] + counts["STARTED"] + counts["RETRY"]
    return counts
