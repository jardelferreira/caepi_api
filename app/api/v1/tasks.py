\
"""Endpoints de consulta do histórico de tarefas armazenado no Redis."""
from fastapi import APIRouter, HTTPException, Query
import redis

from app.services.task_history import list_tasks as get_task_list, task_summary as get_task_summary

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
def list_tasks(
    state: str | None = Query(default=None, max_length=30),
    ca_number: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    try:
        return get_task_list(state=state, ca_number=ca_number, limit=limit, offset=offset)
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="Histórico de tarefas temporariamente indisponível.")


@router.get("/summary")
def task_summary() -> dict[str, int]:
    try:
        return get_task_summary()
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="Resumo de tarefas temporariamente indisponível.")
