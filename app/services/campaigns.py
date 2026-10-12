"""Campanhas de scraping controladas, persistidas no Redis e retomáveis."""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import redis

from app.core.config import settings

logger = logging.getLogger("caepi.campaigns")
_client = redis.Redis.from_url(
    settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
)

CAMPAIGNS_ORDERED = "caepi:campaigns:ordered"
CAMPAIGN_PREFIX = "caepi:campaign:item:"
ACTIVE_CAMPAIGN_KEY = "caepi:campaigns:active"
DISPATCH_LOCK_KEY = "caepi:campaigns:dispatcher-lock"
TASK_PREFIX = "caepi:task_history:item:"
RETENTION_SECONDS = 180 * 24 * 60 * 60
MAX_IN_FLIGHT = 4
TERMINAL_TASK_STATES = {"SUCCESS", "FAILURE", "REVOKED", "ENQUEUE_FAILED"}
ACTIVE_CAMPAIGN_STATES = {"RUNNING", "PAUSED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(campaign_id: str) -> str:
    return f"{CAMPAIGN_PREFIX}{campaign_id}"


def _queue(campaign_id: str) -> str:
    return f"{CAMPAIGN_PREFIX}{campaign_id}:queue"


def _active(campaign_id: str) -> str:
    return f"{CAMPAIGN_PREFIX}{campaign_id}:active-tasks"


def _finished(campaign_id: str) -> str:
    return f"{CAMPAIGN_PREFIX}{campaign_id}:finished-tasks"


def _tasks(campaign_id: str) -> str:
    return f"{CAMPAIGN_PREFIX}{campaign_id}:tasks"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def _decode_campaign(campaign_id: str) -> dict[str, Any] | None:
    data = _client.hgetall(_key(campaign_id))
    if not data:
        return None
    total = _int(data.get("total"))
    remaining = _client.llen(_queue(campaign_id))
    active_count = _client.scard(_active(campaign_id))
    succeeded = _int(data.get("succeeded"))
    failed = _int(data.get("failed"))
    skipped = _int(data.get("skipped"))
    completed = min(total, succeeded + failed + skipped)
    return {
        "campaign_id": campaign_id,
        "state": data.get("state", "UNKNOWN"),
        "scope": data.get("scope", "all"),
        "scope_label": data.get("scope_label", data.get("scope", "all")),
        "operation": data.get("operation", "refresh"),
        "stale_days": _int(data.get("stale_days")) or 90,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "finished_at": data.get("finished_at"),
        "total": total,
        "dispatched": _int(data.get("dispatched")),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "remaining": remaining,
        "active": active_count,
        "completed": completed,
        "progress_pct": round((completed / total) * 100, 1) if total else 100.0,
        "max_in_flight": _int(data.get("max_in_flight")) or MAX_IN_FLIGHT,
    }


def _set_terminal_ttl(campaign_id: str) -> None:
    for key in (_key(campaign_id), _queue(campaign_id), _active(campaign_id), _finished(campaign_id), _tasks(campaign_id)):
        _client.expire(key, RETENTION_SECONDS)


def _release_active_lock(campaign_id: str) -> None:
    if _client.get(ACTIVE_CAMPAIGN_KEY) == campaign_id:
        _client.delete(ACTIVE_CAMPAIGN_KEY)


def _maybe_finish(campaign_id: str) -> None:
    data = _client.hgetall(_key(campaign_id))
    if not data:
        return
    state = data.get("state", "")
    remaining = _client.llen(_queue(campaign_id))
    active_count = _client.scard(_active(campaign_id))
    if state in ACTIVE_CAMPAIGN_STATES and remaining == 0 and active_count == 0:
        final_state = "COMPLETED_WITH_ERRORS" if _int(data.get("failed")) else "COMPLETED"
        _client.hset(_key(campaign_id), mapping={"state": final_state, "updated_at": _now(), "finished_at": _now()})
        _release_active_lock(campaign_id)
        _set_terminal_ttl(campaign_id)
    elif state == "CANCELLED" and active_count == 0:
        _release_active_lock(campaign_id)
        _set_terminal_ttl(campaign_id)


def find_active_campaign() -> dict[str, Any] | None:
    campaign_id = _client.get(ACTIVE_CAMPAIGN_KEY)
    if not campaign_id:
        # Recover the lock after a process restart or a partially-written terminal state.
        for candidate in _client.zrevrange(CAMPAIGNS_ORDERED, 0, 100):
            data = _client.hgetall(_key(candidate))
            if data.get("state") in ACTIVE_CAMPAIGN_STATES or (
                data.get("state") == "CANCELLED" and _client.scard(_active(candidate)) > 0
            ):
                _client.set(ACTIVE_CAMPAIGN_KEY, candidate)
                campaign_id = candidate
                break
    if not campaign_id:
        return None
    data = _client.hgetall(_key(campaign_id))
    state = data.get("state") if data else None
    if state in ACTIVE_CAMPAIGN_STATES or (state == "CANCELLED" and _client.scard(_active(campaign_id)) > 0):
        return _decode_campaign(campaign_id)
    _release_active_lock(campaign_id)
    return None


def create_campaign(
    ca_numbers: list[str], *, scope: str, scope_label: str, operation: str, stale_days: int
) -> dict[str, Any]:
    normalized = list(dict.fromkeys(str(value).strip() for value in ca_numbers if str(value).strip()))
    if not normalized:
        raise ValueError("Não há CAs elegíveis para a campanha.")
    if operation not in {"refresh", "pdf"}:
        raise ValueError("Operação de campanha inválida.")
    campaign_id = str(uuid.uuid4())
    lock = _client.lock("caepi:campaigns:admission-lock", timeout=10, blocking_timeout=3)
    acquired = lock.acquire(blocking=True)
    if not acquired:
        raise RuntimeError("Não foi possível reservar a criação de campanha; tente novamente.")
    try:
        current = find_active_campaign()
        if current:
            raise RuntimeError(f"Já existe uma campanha ativa ou pausada ({current['campaign_id']}).")
        now = _now()
        pipe = _client.pipeline(transaction=True)
        pipe.hset(_key(campaign_id), mapping={
            "campaign_id": campaign_id,
            "state": "RUNNING",
            "scope": scope,
            "scope_label": scope_label,
            "operation": operation,
            "stale_days": str(stale_days),
            "created_at": now,
            "updated_at": now,
            "total": str(len(normalized)),
            "dispatched": "0",
            "succeeded": "0",
            "failed": "0",
            "skipped": "0",
            "max_in_flight": str(MAX_IN_FLIGHT),
        })
        pipe.rpush(_queue(campaign_id), *normalized)
        pipe.zadd(CAMPAIGNS_ORDERED, {campaign_id: time.time()})
        pipe.set(ACTIVE_CAMPAIGN_KEY, campaign_id)
        pipe.execute()
    finally:
        try:
            lock.release()
        except redis.RedisError:
            pass
    return _decode_campaign(campaign_id) or {"campaign_id": campaign_id, "state": "RUNNING", "total": len(normalized)}


def list_campaigns(*, limit: int = 20) -> list[dict[str, Any]]:
    ids = _client.zrevrange(CAMPAIGNS_ORDERED, 0, max(0, limit - 1))
    results = []
    for campaign_id in ids:
        info = _decode_campaign(campaign_id)
        if info:
            _maybe_finish(campaign_id)
            info = _decode_campaign(campaign_id) or info
            results.append(info)
    return results


def get_campaign(campaign_id: str) -> dict[str, Any] | None:
    _maybe_finish(campaign_id)
    return _decode_campaign(campaign_id)


def list_campaign_tasks(campaign_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    ids = _client.zrevrange(_tasks(campaign_id), 0, max(0, min(limit, 200) - 1))
    items: list[dict[str, Any]] = []
    for task_id in ids:
        values = _client.hgetall(f"{TASK_PREFIX}{task_id}")
        if not values:
            continue
        item: dict[str, Any] = dict(values)
        for field in ("duration_ms", "retries"):
            if field in item:
                item[field] = _int(item[field])
        if item.get("result_summary"):
            try:
                item["result_summary"] = json.loads(item["result_summary"])
            except (ValueError, TypeError):
                pass
        item["operation"] = "pdf" if item.get("task_name", "").endswith("fetch_certificate_pdf") else "refresh"
        items.append(item)
    return items


def list_failed_cas(campaign_id: str, *, limit: int = 100) -> list[str]:
    ids = _client.zrevrange(_tasks(campaign_id), 0, -1)
    if not ids:
        return []
    pipe = _client.pipeline(transaction=False)
    for task_id in ids:
        pipe.hmget(f"{TASK_PREFIX}{task_id}", "state", "ca_number")
    failed: list[str] = []
    for values in pipe.execute():
        state, ca_number = values or (None, None)
        if str(state or "").upper() in {"FAILURE", "ENQUEUE_FAILED"} and ca_number and ca_number not in failed:
            failed.append(ca_number)
            if len(failed) >= limit:
                break
    return failed


def list_campaign_report_tasks(campaign_id: str, *, limit: int = 50000) -> list[dict[str, Any]]:
    """Retorna o relatório operacional completo usando leituras Redis em pipeline."""
    ids = _client.zrange(_tasks(campaign_id), 0, max(0, min(limit, 50000) - 1))
    if not ids:
        return []
    pipe = _client.pipeline(transaction=False)
    for task_id in ids:
        pipe.hgetall(f"{TASK_PREFIX}{task_id}")
    records: list[dict[str, Any]] = []
    for task_id, values in zip(ids, pipe.execute()):
        if not values:
            continue
        item: dict[str, Any] = dict(values)
        item.setdefault("task_id", task_id)
        item["operation"] = "pdf" if item.get("task_name", "").endswith("fetch_certificate_pdf") else "refresh"
        if item.get("result_summary"):
            try:
                result = json.loads(item["result_summary"])
                item["result_status"] = result.get("status", "") if isinstance(result, dict) else ""
            except (ValueError, TypeError):
                item["result_status"] = ""
        records.append(item)
    return records


def pause_campaign(campaign_id: str) -> dict[str, Any] | None:
    current = get_campaign(campaign_id)
    if not current or current["state"] != "RUNNING":
        return None
    _client.hset(_key(campaign_id), mapping={"state": "PAUSED", "updated_at": _now()})
    return get_campaign(campaign_id)


def resume_campaign(campaign_id: str) -> dict[str, Any] | None:
    current = get_campaign(campaign_id)
    if not current or current["state"] != "PAUSED":
        return None
    _client.hset(_key(campaign_id), mapping={"state": "RUNNING", "updated_at": _now()})
    _client.set(ACTIVE_CAMPAIGN_KEY, campaign_id)
    return get_campaign(campaign_id)


def cancel_campaign(campaign_id: str) -> dict[str, Any] | None:
    current = get_campaign(campaign_id)
    if not current or current["state"] not in ACTIVE_CAMPAIGN_STATES:
        return None
    remaining = _client.llen(_queue(campaign_id))
    pipe = _client.pipeline(transaction=True)
    pipe.delete(_queue(campaign_id))
    pipe.hincrby(_key(campaign_id), "skipped", remaining)
    pipe.hset(_key(campaign_id), mapping={"state": "CANCELLED", "updated_at": _now(), "cancelled_at": _now()})
    pipe.execute()
    _maybe_finish(campaign_id)
    return get_campaign(campaign_id)


def record_task_terminal(campaign_id: str, task_id: str, state: str) -> None:
    """Conta um resultado terminal exatamente uma vez, mesmo se dois Celery signals chegarem."""
    normalized_state = (state or "").upper()
    if normalized_state not in TERMINAL_TASK_STATES:
        return
    done_key = _finished(campaign_id)
    try:
        if not _client.exists(_key(campaign_id)):
            return
        if not _client.sadd(done_key, task_id):
            return
        _client.srem(_active(campaign_id), task_id)
        counter = "succeeded" if normalized_state == "SUCCESS" else "failed"
        _client.hincrby(_key(campaign_id), counter, 1)
        _client.hset(_key(campaign_id), "updated_at", _now())
        _maybe_finish(campaign_id)
    except redis.RedisError:
        logger.exception("campaign_task_update_failed", extra={"campaign_id": campaign_id, "task_id": task_id})


def _dispatch_one(campaign_id: str, ca_number: str, task_function) -> bool:
    from app.services.task_history import register_queued, update_task

    task_id = str(uuid.uuid4())
    register_queued(task_id, task_function.name, ca_number, batch_id=campaign_id)
    pipe = _client.pipeline(transaction=True)
    pipe.sadd(_active(campaign_id), task_id)
    pipe.zadd(_tasks(campaign_id), {task_id: time.time()})
    pipe.hset(_key(campaign_id), "updated_at", _now())
    pipe.execute()
    try:
        task_function.apply_async(args=[ca_number], task_id=task_id)
        _client.hincrby(_key(campaign_id), "dispatched", 1)
        _client.hset(_key(campaign_id), "updated_at", _now())
        return True
    except Exception as exc:
        logger.exception("campaign_task_enqueue_failed", extra={"campaign_id": campaign_id, "ca_number": ca_number})
        update_task(
            task_id, state="FAILURE", task_name=task_function.name, ca_number=ca_number,
            finished_at=datetime.now(timezone.utc), error_type=type(exc).__name__,
            error_message="Não foi possível publicar esta tarefa no Celery.",
        )
        return False


def dispatch_campaign_work(max_dispatch_per_tick: int = 20) -> dict[str, int]:
    """Alimenta gradualmente o Celery, mantendo poucas tarefas enfileiradas/em execução."""
    token = str(uuid.uuid4())
    if not _client.set(DISPATCH_LOCK_KEY, token, nx=True, ex=30):
        return {"campaigns_seen": 0, "dispatched": 0}
    seen = dispatched = attempts = 0
    try:
        from app.workers.tasks import fetch_certificate_pdf, refresh_source

        for campaign_id in _client.zrevrange(CAMPAIGNS_ORDERED, 0, 49):
            data = _client.hgetall(_key(campaign_id))
            state = data.get("state")
            if state not in ACTIVE_CAMPAIGN_STATES:
                _maybe_finish(campaign_id)
                continue
            seen += 1
            task_function = refresh_source if data.get("operation") == "refresh" else fetch_certificate_pdf
            while attempts < max_dispatch_per_tick:
                data = _client.hgetall(_key(campaign_id))
                if data.get("state") != "RUNNING":
                    break
                active_count = _client.scard(_active(campaign_id))
                if active_count >= MAX_IN_FLIGHT:
                    break
                ca_number = _client.lindex(_queue(campaign_id), -1)
                if ca_number is None:
                    break
                # Keep the item in the queue until history and broker publication succeed.
                published = _dispatch_one(campaign_id, ca_number, task_function)
                _client.lrem(_queue(campaign_id), 1, ca_number)
                attempts += 1
                if published:
                    dispatched += 1
            _maybe_finish(campaign_id)
            if attempts >= max_dispatch_per_tick:
                break
        return {"campaigns_seen": seen, "dispatched": dispatched, "attempts": attempts}
    except redis.RedisError:
        logger.exception("campaign_dispatcher_redis_error")
        return {"campaigns_seen": seen, "dispatched": dispatched}
    finally:
        # Compare-and-delete avoids releasing another dispatcher's lock.
        try:
            _client.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1, DISPATCH_LOCK_KEY, token,
            )
        except redis.RedisError:
            pass


def retry_failed_campaign(campaign_id: str) -> dict[str, Any] | None:
    source = get_campaign(campaign_id)
    if not source:
        return None
    failed = list_failed_cas(campaign_id, limit=50000)
    if not failed:
        return {"campaign_id": campaign_id, "no_failures_to_retry": True}
    return create_campaign(
        failed,
        scope=f"retry_failed:{campaign_id}",
        scope_label=f"Reprocessar falhas de {campaign_id[:8]}",
        operation=source["operation"],
        stale_days=source["stale_days"],
    )
