"""Endpoints de consulta, atualização e processamento controlado de certificados CAEPI."""

import csv
import io
import logging
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Literal

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.core.database import SessionLocal
from app.models.ca import Certificate
from app.services.task_history import register_queued
from app.services.campaigns import (
    cancel_campaign as cancel_bulk_campaign,
    create_campaign as create_bulk_campaign,
    find_active_campaign,
    get_campaign as get_bulk_campaign,
    list_campaigns as list_bulk_campaigns,
    list_campaign_tasks,
    list_campaign_report_tasks,
    list_failed_cas,
    pause_campaign as pause_bulk_campaign,
    resume_campaign as resume_bulk_campaign,
    retry_failed_campaign,
)
from app.workers.celery_app import celery_app
from app.workers.tasks import fetch_certificate_pdf, refresh_source

logger = logging.getLogger("caepi.api.certificates")
router = APIRouter(prefix="/certificates", tags=["certificates"])

BATCH_MAX_UNIQUE_CAS = 10
BATCH_MAX_RAW_VALUES = 30
DEFAULT_STALE_DAYS = 90

_COMPARISON_FIELDS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "status", "Situação", ("situacao", "status", "situacaoca"),
        ("situacao", "status"),
    ),
    (
        "valid_until", "Validade",
        ("datavalidade", "datadevalidade", "validade", "validuntil"),
        ("data_validade", "valid_until", "validade"),
    ),
    (
        "manufacturer", "Fabricante",
        ("razaosocial", "fabricante", "razaosocialfabricante"),
        ("razao_social", "manufacturer", "fabricante"),
    ),
    (
        "equipment_name", "Equipamento",
        ("nomeequipamento", "equipamento", "nomeepi"),
        ("equipamento", "equipment_name", "nomeequipamento"),
    ),
    (
        "brand", "Marca", ("marcaca", "marca", "marcaepi"),
        ("marca", "brand", "marcaca"),
    ),
)


def _normalize_integrity_text(value: Any) -> str:
    """Normaliza strings para comparar fontes sem diferenças de acentos/espaços."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", text).casefold().split())


def _parse_integrity_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    raw = raw.split(" ", 1)[0].split("T", 1)[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _normalized_mapping(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    result: dict[str, Any] = {}
    for key, value in data.items():
        normalized = _normalize_integrity_text(key).replace(" ", "")
        if normalized:
            result[normalized] = value
    return result


def _first_nonempty(mapping: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        value = mapping.get(alias)
        if value is not None and str(value).strip():
            return value
    return None


def _certificate_integrity(
    certificate: Certificate, *, today: date, stale_days: int
) -> dict[str, Any]:
    """Identifica alertas conservadoras sem modificar o registro do certificado."""
    data = certificate.consolidated_data if isinstance(certificate.consolidated_data, dict) else {}
    official = _normalized_mapping(data.get("official_base"))
    issues: list[dict[str, Any]] = []

    normalized_status = _normalize_integrity_text(certificate.status).replace(" ", "")
    if (
        normalized_status in {"valido", "valida"}
        and certificate.valid_until
        and certificate.valid_until < today
    ):
        issues.append({
            "code": "status_validity_conflict",
            "label": "Situação/validade conflitante",
            "detail": f"Situação '{certificate.status}' com validade em {certificate.valid_until.isoformat()}.",
        })

    differing_fields: list[dict[str, str]] = []
    for field, label, official_aliases, portal_aliases in _COMPARISON_FIELDS:
        official_value = _first_nonempty(official, official_aliases)
        portal_value = _first_nonempty(data, portal_aliases)
        if official_value is None or portal_value is None:
            continue
        if field == "valid_until":
            official_date = _parse_integrity_date(official_value)
            portal_date = _parse_integrity_date(portal_value)
            if official_date is not None and portal_date is not None:
                differs = official_date != portal_date
            else:
                differs = _normalize_integrity_text(official_value) != _normalize_integrity_text(portal_value)
        else:
            differs = _normalize_integrity_text(official_value) != _normalize_integrity_text(portal_value)
        if differs:
            differing_fields.append({
                "field": field,
                "label": label,
                "official": str(official_value)[:160],
                "portal": str(portal_value)[:160],
            })

    if differing_fields:
        issues.append({
            "code": "official_portal_divergence",
            "label": "Divergência entre fontes",
            "detail": f"{len(differing_fields)} campo(s) divergem entre base oficial e portal.",
            "fields": differing_fields,
        })

    last_update = certificate.last_source_update
    is_recent_portal = certificate.last_source == "caepi_portal" and last_update is not None
    if is_recent_portal:
        # O timestamp pode vir sem timezone em bancos/fixtures antigos.
        last_update_date = last_update.date() if isinstance(last_update, datetime) else None
        is_recent_portal = bool(
            last_update_date and last_update_date >= today - timedelta(days=stale_days)
        )
    if not is_recent_portal:
        issues.append({
            "code": "portal_stale",
            "label": "Sem consulta recente confirmada",
            "detail": f"Não há consulta ao portal CAEPI confirmada nos últimos {stale_days} dias.",
        })

    codes = [issue["code"] for issue in issues]
    return {
        "issues": issues,
        "issue_codes": codes,
        "issue_count": len(issues),
        "has_issue": bool(issues),
        "comparison_fields": differing_fields,
    }


class TaskAccepted(BaseModel):
    task_id: str
    status: str = "queued"
    ca_number: str


class BatchTaskRequest(BaseModel):
    """Batch pequeno e explícito; a API nunca varre a base inteira sozinha."""

    ca_numbers: list[str] = Field(min_length=1, max_length=BATCH_MAX_RAW_VALUES)
    operation: Literal["refresh", "pdf"] = "refresh"


class BulkCampaignRequest(BaseModel):
    """Campanha explícita com snapshot dos CAs elegíveis, limitada a 50 mil registros."""

    scope: Literal[
        "unqueried", "status_validity_conflict", "official_portal_divergence", "any_issue", "all"
    ] = "unqueried"
    operation: Literal["refresh", "pdf"] = "refresh"
    stale_days: int = Field(default=DEFAULT_STALE_DAYS, ge=1, le=3650)
    max_cas: int = Field(default=100, ge=1, le=50000)
    expected_eligible_count: int | None = Field(default=None, ge=0, le=1000000)
    confirmed: bool = False


CAMPAIGN_SCOPE_LABELS = {
    "unqueried": "Sem consulta recente ao portal",
    "status_validity_conflict": "Situação incompatível com validade",
    "official_portal_divergence": "Divergência entre fontes",
    "any_issue": "Qualquer alerta de integridade",
    "all": "Todos os CAs cadastrados",
}


def _campaign_candidates(scope: str, stale_days: int) -> list[str]:
    """Retorna o snapshot ordenado de candidatos sem executar scraping nem gravar dados."""
    today = date.today()
    with SessionLocal() as db:
        certificates = db.scalars(select(Certificate).order_by(Certificate.ca_number.asc())).all()
    numbers: list[str] = []
    for certificate in certificates:
        codes = set(_certificate_integrity(certificate, today=today, stale_days=stale_days)["issue_codes"])
        if scope == "all":
            matches = True
        elif scope == "unqueried":
            matches = "portal_stale" in codes
        elif scope == "status_validity_conflict":
            matches = "status_validity_conflict" in codes
        elif scope == "official_portal_divergence":
            matches = "official_portal_divergence" in codes
        elif scope == "any_issue":
            matches = bool(codes)
        else:
            matches = False
        if matches:
            numbers.append(certificate.ca_number)
    return numbers



def _validate_ca_number(value: str) -> str:
    normalized = re.sub(r"\D", "", value or "")
    if not normalized or len(normalized) > 32:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Informe um número de CA válido.",
        )
    return normalized


def _enqueue(
    task_function, normalized: str, *, batch_id: str | None = None
) -> TaskAccepted:
    task = task_function.delay(normalized)
    # O worker pode começar antes desta escrita; register_queued não regride o estado.
    register_queued(task.id, task_function.name, normalized, batch_id=batch_id)
    logger.info(
        "certificate_task_enqueued",
        extra={
            "event": "task_enqueued",
            "task_name": task_function.name,
            "task_id": task.id,
            "ca_number": normalized,
        },
    )
    return TaskAccepted(task_id=task.id, ca_number=normalized)


@router.get("/catalog")
def certificate_catalog(
    q: str | None = Query(default=None, max_length=120),
    unqueried_only: bool | None = Query(default=None),
    status_filter: str | None = Query(default=None, max_length=80),
    validity_filter: Literal["all", "current", "expired", "undated"] = Query(default="all"),
    source_filter: Literal["all", "official", "portal", "unqueried"] = Query(default="unqueried"),
    integrity_filter: Literal[
        "all", "any", "status_validity_conflict", "official_portal_divergence", "portal_stale"
    ] = Query(default="all"),
    stale_days: int = Query(default=DEFAULT_STALE_DAYS, ge=1, le=3650),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Lista e filtra CAs locais para pesquisa e seleção manual em lotes pequenos.

    ``unqueried_only`` é mantido por compatibilidade com versões anteriores da UI.
    A pesquisa textual aceita número/prefixo do CA, fabricante, marca ou equipamento.
    """
    predicates = []
    query_text = (q or "").strip()
    if query_text:
        normalized_query = re.sub(r"\D", "", query_text)
        search_terms = []
        if query_text.isdigit() and normalized_query:
            search_terms.append(Certificate.ca_number.like(f"{normalized_query}%"))
        pattern = f"%{query_text}%"
        search_terms.extend([
            Certificate.manufacturer.ilike(pattern),
            Certificate.brand.ilike(pattern),
            Certificate.equipment_name.ilike(pattern),
            Certificate.equipment_description.ilike(pattern),
        ])
        predicates.append(or_(*search_terms))

    if status_filter and status_filter.strip():
        # A interface popula o seletor com os valores existentes no banco; a comparação
        # exata preserva caracteres acentuados também em ambientes SQLite de teste.
        predicates.append(Certificate.status == status_filter.strip())

    today = date.today()
    if validity_filter == "current":
        predicates.append(Certificate.valid_until >= today)
    elif validity_filter == "expired":
        predicates.append(Certificate.valid_until < today)
    elif validity_filter == "undated":
        predicates.append(Certificate.valid_until.is_(None))

    # Compatibilidade: clientes antigos podem continuar enviando unqueried_only.
    if unqueried_only is not None:
        source_filter = "unqueried" if unqueried_only else "all"

    if source_filter == "official":
        predicates.append(Certificate.last_source == "mte_official_base")
    elif source_filter == "portal":
        predicates.append(Certificate.last_source == "caepi_portal")
    elif source_filter == "unqueried":
        predicates.append(
            or_(Certificate.last_source.is_(None), Certificate.last_source != "caepi_portal")
        )

    with SessionLocal() as db:
        count_statement = select(func.count()).select_from(Certificate)
        if predicates:
            count_statement = count_statement.where(*predicates)
        sql_total = int(db.scalar(count_statement) or 0)

        statement = select(Certificate)
        if predicates:
            statement = statement.where(*predicates)
        if integrity_filter == "all":
            total = sql_total
            certificates = db.scalars(
                statement.order_by(Certificate.ca_number.asc()).offset(offset).limit(limit)
            ).all()
        else:
            # O conteúdo oficial fica em JSON e seus cabeçalhos variam entre fontes; a
            # avaliação de integridade é feita em Python para manter compatibilidade
            # PostgreSQL/SQLite e não introduzir expressões JSON específicas do banco.
            matched: list[Certificate] = []
            for candidate in db.scalars(statement.order_by(Certificate.ca_number.asc())).all():
                integrity = _certificate_integrity(candidate, today=today, stale_days=stale_days)
                codes = integrity["issue_codes"]
                if integrity_filter == "any" and codes:
                    matched.append(candidate)
                elif integrity_filter in codes:
                    matched.append(candidate)
            total = len(matched)
            certificates = matched[offset:offset + limit]

        status_options = db.scalars(
            select(Certificate.status)
            .where(Certificate.status.is_not(None), Certificate.status != "")
            .distinct()
            .order_by(Certificate.status.asc())
        ).all()

    items = []
    for item in certificates:
        data = item.consolidated_data or {}
        official_data = data.get("official_base") if isinstance(data, dict) else None
        integrity = _certificate_integrity(item, today=today, stale_days=stale_days)
        items.append({
            "ca_number": item.ca_number,
            "status": item.status,
            "valid_until": item.valid_until.isoformat() if item.valid_until else None,
            "manufacturer": item.manufacturer,
            "equipment_name": item.equipment_name,
            "brand": item.brand,
            "last_source": item.last_source,
            "last_source_update": (
                item.last_source_update.isoformat() if item.last_source_update else None
            ),
            "has_official_base": isinstance(official_data, dict) and bool(official_data),
            "integrity_issues": integrity["issues"],
            "integrity_issue_codes": integrity["issue_codes"],
            "integrity_issue_count": integrity["issue_count"],
        })

    clean_status_options = sorted(
        {str(value).strip() for value in status_options if value and str(value).strip()},
        key=str.casefold,
    )
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "query": query_text,
        "status_filter": status_filter or "",
        "validity_filter": validity_filter,
        "source_filter": source_filter,
        "integrity_filter": integrity_filter,
        "stale_days": stale_days,
        "status_options": clean_status_options,
        "batch_max_unique_cas": BATCH_MAX_UNIQUE_CAS,
    }


@router.get("/integrity/summary")
def certificate_integrity_summary(
    stale_days: int = Query(default=DEFAULT_STALE_DAYS, ge=1, le=3650),
) -> dict[str, Any]:
    """Resumo de integridade da base; somente leitura, sem alterar certificados."""
    today = date.today()
    with SessionLocal() as db:
        certificates = db.scalars(select(Certificate)).all()
        total = len(certificates)
        counts = {
            "status_validity_conflict": 0,
            "official_portal_divergence": 0,
            "portal_stale": 0,
            "any_issue": 0,
        }
        for certificate in certificates:
            report = _certificate_integrity(certificate, today=today, stale_days=stale_days)
            codes = set(report["issue_codes"])
            for code in counts:
                if code == "any_issue":
                    if codes:
                        counts[code] += 1
                elif code in codes:
                    counts[code] += 1
    return {
        "total_certificates": total,
        "stale_days": stale_days,
        "checked_at": datetime.now().astimezone().isoformat(),
        **counts,
    }


@router.get("/campaigns/preview")
def preview_bulk_campaign(
    scope: Literal[
        "unqueried", "status_validity_conflict", "official_portal_divergence", "any_issue", "all"
    ] = Query(default="unqueried"),
    stale_days: int = Query(default=DEFAULT_STALE_DAYS, ge=1, le=3650),
) -> dict[str, Any]:
    """Conta candidatos para campanha; é somente leitura e nunca enfileira tarefas."""
    try:
        candidates = _campaign_candidates(scope, stale_days)
        active = find_active_campaign()
    except Exception as exc:
        logger.exception("bulk_campaign_preview_failed", extra={"scope": scope})
        raise HTTPException(status_code=503, detail="Não foi possível calcular os CAs elegíveis.") from exc
    return {
        "scope": scope,
        "scope_label": CAMPAIGN_SCOPE_LABELS[scope],
        "stale_days": stale_days,
        "eligible_count": len(candidates),
        "sample_ca_numbers": candidates[:10],
        "active_campaign": active,
        "safe_defaults": {"max_cas": 100, "max_in_flight": 4},
        "automatic_execution": False,
    }


@router.post("/campaigns", status_code=status.HTTP_202_ACCEPTED)
def create_bulk_campaign_endpoint(request: BulkCampaignRequest) -> dict[str, Any]:
    """Cria campanha apenas após confirmação explícita e congela a lista de CAs elegíveis."""
    if not request.confirmed:
        raise HTTPException(status_code=400, detail="Confirme explicitamente a criação da campanha.")
    try:
        candidates = _campaign_candidates(request.scope, request.stale_days)
        eligible_count = len(candidates)
        if request.expected_eligible_count is not None and request.expected_eligible_count != eligible_count:
            raise HTTPException(
                status_code=409,
                detail=(f"A quantidade elegível mudou de {request.expected_eligible_count} para "
                        f"{eligible_count}. Atualize a prévia antes de iniciar."),
            )
        selected = candidates[:request.max_cas]
        if not selected:
            raise HTTPException(status_code=422, detail="Não há CAs elegíveis para esta campanha.")
        campaign = create_bulk_campaign(
            selected, scope=request.scope, scope_label=CAMPAIGN_SCOPE_LABELS[request.scope],
            operation=request.operation, stale_days=request.stale_days,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("bulk_campaign_create_failed", extra={"scope": request.scope})
        raise HTTPException(status_code=503, detail="Não foi possível criar a campanha. Verifique Redis.") from exc
    return {**campaign, "eligible_count": eligible_count, "selected_count": len(selected), "automatic_execution": False}


@router.get("/campaigns")
def get_bulk_campaigns() -> dict[str, Any]:
    try:
        return {"items": list_bulk_campaigns(limit=25), "active_campaign": find_active_campaign()}
    except Exception as exc:
        logger.exception("bulk_campaign_list_failed")
        raise HTTPException(status_code=503, detail="Não foi possível consultar as campanhas.") from exc


@router.get("/campaigns/{campaign_id}")
def get_bulk_campaign_endpoint(campaign_id: str) -> dict[str, Any]:
    try:
        campaign = get_bulk_campaign(str(uuid.UUID(campaign_id)))
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campanha não encontrada.")
        campaign["tasks"] = list_campaign_tasks(campaign_id, limit=100)
        campaign["failure_cas"] = list_failed_cas(campaign_id, limit=500)
        return campaign
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Identificador de campanha inválido.") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("bulk_campaign_detail_failed", extra={"campaign_id": campaign_id})
        raise HTTPException(status_code=503, detail="Não foi possível consultar esta campanha.") from exc


@router.get("/campaigns/{campaign_id}/report.csv")
def export_bulk_campaign_report(campaign_id: str) -> Response:
    """Baixa o relatório completo da campanha como CSV compatível com Excel."""
    try:
        normalized_id = str(uuid.UUID(campaign_id))
        campaign = get_bulk_campaign(normalized_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campanha não encontrada.")
        records = list_campaign_report_tasks(normalized_id, limit=50000)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Identificador de campanha inválido.") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("bulk_campaign_report_failed", extra={"campaign_id": campaign_id})
        raise HTTPException(status_code=503, detail="Não foi possível gerar o relatório.") from exc

    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow([
        "CA", "Operação", "Estado", "Resultado", "Criada em", "Iniciada em",
        "Finalizada em", "Duração (ms)", "Tentativas", "Tipo de erro", "Mensagem",
        "ID da tarefa",
    ])
    for item in records:
        writer.writerow([
            item.get("ca_number", ""), item.get("operation", "refresh"), item.get("state", ""),
            item.get("result_status", ""), item.get("created_at", ""), item.get("started_at", ""),
            item.get("finished_at", ""), item.get("duration_ms", ""), item.get("retries", ""),
            item.get("error_type", ""), item.get("error_message", ""), item.get("task_id", ""),
        ])
    content = "\ufeff" + output.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="caepi-campaign-{normalized_id[:8]}.csv"'},
    )


@router.post("/campaigns/{campaign_id}/pause")
def pause_bulk_campaign_endpoint(campaign_id: str) -> dict[str, Any]:
    try:
        result = pause_bulk_campaign(str(uuid.UUID(campaign_id)))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Identificador de campanha inválido.") from exc
    if result is None:
        raise HTTPException(status_code=409, detail="A campanha não está em execução.")
    return result


@router.post("/campaigns/{campaign_id}/resume")
def resume_bulk_campaign_endpoint(campaign_id: str) -> dict[str, Any]:
    try:
        result = resume_bulk_campaign(str(uuid.UUID(campaign_id)))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Identificador de campanha inválido.") from exc
    if result is None:
        raise HTTPException(status_code=409, detail="A campanha não está pausada.")
    return result


@router.post("/campaigns/{campaign_id}/cancel")
def cancel_bulk_campaign_endpoint(campaign_id: str) -> dict[str, Any]:
    try:
        result = cancel_bulk_campaign(str(uuid.UUID(campaign_id)))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Identificador de campanha inválido.") from exc
    if result is None:
        raise HTTPException(status_code=409, detail="A campanha não pode ser cancelada neste estado.")
    return result


@router.post("/campaigns/{campaign_id}/retry-failed", status_code=status.HTTP_202_ACCEPTED)
def retry_failed_bulk_campaign_endpoint(campaign_id: str) -> dict[str, Any]:
    try:
        result = retry_failed_campaign(str(uuid.UUID(campaign_id)))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Identificador de campanha inválido.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("bulk_campaign_retry_failed", extra={"campaign_id": campaign_id})
        raise HTTPException(status_code=503, detail="Não foi possível reprocessar as falhas.") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Campanha não encontrada.")
    if result.get("no_failures_to_retry"):
        raise HTTPException(status_code=409, detail="Esta campanha não possui falhas para reprocessar.")
    return result


@router.post("/batch", status_code=status.HTTP_202_ACCEPTED)
def enqueue_batch(request: BatchTaskRequest) -> dict[str, Any]:
    """Enfileira até 10 CAs existentes; operação e seleção sempre explícitas."""
    normalized_numbers: list[str] = []
    seen: set[str] = set()
    for raw in request.ca_numbers:
        normalized = _validate_ca_number(raw)
        if normalized not in seen:
            normalized_numbers.append(normalized)
            seen.add(normalized)

    if not normalized_numbers:
        raise HTTPException(status_code=422, detail="Selecione ao menos um CA.")
    if len(normalized_numbers) > BATCH_MAX_UNIQUE_CAS:
        raise HTTPException(
            status_code=422,
            detail=f"O limite por lote é de {BATCH_MAX_UNIQUE_CAS} CAs únicos.",
        )

    # Lotes só podem incluir CAs presentes na base previamente importada/cadastrada.
    with SessionLocal() as db:
        existing_numbers = set(db.scalars(
            select(Certificate.ca_number).where(Certificate.ca_number.in_(normalized_numbers))
        ).all())

    missing = [number for number in normalized_numbers if number not in existing_numbers]
    if missing:
        preview = ", ".join(missing[:10])
        suffix = "…" if len(missing) > 10 else ""
        raise HTTPException(
            status_code=422,
            detail=f"Estes CAs não estão na base local importada: {preview}{suffix}",
        )

    task_function = refresh_source if request.operation == "refresh" else fetch_certificate_pdf
    batch_id = str(uuid.uuid4())
    items: list[dict[str, Any]] = []
    for ca_number in normalized_numbers:
        try:
            accepted = _enqueue(task_function, ca_number, batch_id=batch_id)
            items.append({
                "ca_number": ca_number,
                "task_id": accepted.task_id,
                "status": "queued",
                "operation": request.operation,
            })
        except Exception as exc:  # A falha de uma publicação não impede o restante do lote.
            logger.exception(
                "certificate_batch_enqueue_failed",
                extra={"event": "batch_enqueue_failed", "batch_id": batch_id,
                         "ca_number": ca_number, "operation": request.operation,
                         "exception_type": type(exc).__name__},
            )
            items.append({
                "ca_number": ca_number,
                "task_id": None,
                "status": "enqueue_failed",
                "operation": request.operation,
                "error": "Não foi possível colocar esta tarefa na fila.",
            })

    queued = sum(1 for item in items if item["status"] == "queued")
    failed = len(items) - queued
    if queued == 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Nenhuma tarefa do lote pôde ser colocada na fila. Verifique Redis/Celery.",
        )

    logger.info(
        "certificate_batch_enqueued",
        extra={"event": "batch_enqueued", "batch_id": batch_id,
               "operation": request.operation, "queued": queued, "failed": failed},
    )
    return {
        "batch_id": batch_id,
        "operation": request.operation,
        "requested": len(normalized_numbers),
        "queued": queued,
        "enqueue_failed": failed,
        "max_unique_cas": BATCH_MAX_UNIQUE_CAS,
        "items": items,
    }


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
        # Evita expor stack traces/segredos do worker na API pública.
        response["error"] = "task_failed"
    return response
