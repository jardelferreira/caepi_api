"""Sessão administrativa, consulta no painel e gerenciamento de chaves de integração."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.api_key import ApiKey
from app.models.ca import Certificate
from app.models.document import CertificateDocument
from app.security.auth import ALLOWED_SCOPES, require_admin_access, require_admin_session


login_router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])
admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_access)],
)
MAX_FAILED_LOGINS = 10
LOGIN_WINDOW_SECONDS = 15 * 60


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    scopes: list[Literal["certificates:read", "documents:read"]] = Field(
        default_factory=lambda: ["certificates:read"]
    )
    expires_in_days: int | None = Field(default=365, ge=1, le=3650)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(value))
        if not cleaned or not set(cleaned).issubset(ALLOWED_SCOPES):
            raise ValueError("Selecione ao menos um escopo permitido.")
        return cleaned


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    parsed = urlsplit(origin)
    expected_host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    expected_scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    if parsed.netloc.lower() != expected_host.lower() or parsed.scheme.lower() != expected_scheme.lower():
        raise HTTPException(status_code=403, detail="Origem da solicitação não autorizada.")


def _login_attempt_key(request: Request, username: str) -> str:
    client_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    value = f"{client_ip.strip()}|{username.strip().casefold()}"
    return "caepi:admin:login-fail:" + hashlib.sha256(value.encode()).hexdigest()


def _increment_login_failures(request: Request, username: str) -> int:
    if settings.app_env == "test":
        return 1
    try:
        client = redis.Redis.from_url(
            settings.redis_url, decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2,
        )
        key = _login_attempt_key(request, username)
        count = int(client.incr(key))
        if count == 1:
            client.expire(key, LOGIN_WINDOW_SECONDS)
        return count
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Controle de acesso temporariamente indisponível. Tente novamente.") from exc


def _clear_login_failures(request: Request, username: str) -> None:
    if settings.app_env == "test":
        return
    try:
        client = redis.Redis.from_url(
            settings.redis_url, decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2,
        )
        client.delete(_login_attempt_key(request, username))
    except redis.RedisError:
        # A autenticação concluída não é desfeita porque a limpeza do contador falhou.
        pass


def _serialize_certificate(db, certificate: Certificate) -> dict:
    data = certificate.consolidated_data if isinstance(certificate.consolidated_data, dict) else {}
    meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
    sources = meta.get("sources") if isinstance(meta.get("sources"), dict) else {}
    documents = db.scalars(
        select(CertificateDocument)
        .where(CertificateDocument.certificate_id == certificate.id)
        .order_by(CertificateDocument.created_at.desc())
    ).all()
    latest_pdf = next(
        (doc for doc in documents if doc.document_type == "certificate_pdf" and doc.status == "downloaded" and doc.object_key),
        None,
    )
    return {
        "ca_number": certificate.ca_number,
        "status": certificate.status,
        "valid_until": certificate.valid_until.isoformat() if certificate.valid_until else None,
        "manufacturer_document": certificate.manufacturer_document,
        "manufacturer": certificate.manufacturer,
        "equipment_name": certificate.equipment_name,
        "equipment_description": certificate.equipment_description,
        "brand": certificate.brand,
        "protection_details": certificate.protection_details,
        "last_source": certificate.last_source,
        "last_source_update": certificate.last_source_update.isoformat() if certificate.last_source_update else None,
        "created_at": certificate.created_at.isoformat() if certificate.created_at else None,
        "updated_at": certificate.updated_at.isoformat() if certificate.updated_at else None,
        "sources": sources,
        "data": {key: value for key, value in data.items() if key != "_meta"},
        "documents": {
            "certificate_pdf_available": latest_pdf is not None,
            "certificate_pdf_sha256": latest_pdf.sha256 if latest_pdf else None,
            "certificate_pdf_size_bytes": latest_pdf.size_bytes if latest_pdf else None,
            "certificate_pdf_last_checked_at": latest_pdf.last_checked_at.isoformat() if latest_pdf and latest_pdf.last_checked_at else None,
        },
    }


@login_router.post("/login")
def login(payload: LoginRequest, request: Request) -> dict:
    if not (settings.admin_username and settings.admin_password and settings.admin_session_secret and len(settings.admin_session_secret) >= 32):
        raise HTTPException(
            status_code=503,
            detail="Login desabilitado. Configure ADMIN_USERNAME, ADMIN_PASSWORD e ADMIN_SESSION_SECRET no arquivo .env.",
        )
    _check_origin(request)
    failures = _increment_login_failures(request, payload.username)
    if failures > MAX_FAILED_LOGINS:
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde 15 minutos antes de tentar novamente.")

    valid_user = secrets.compare_digest(payload.username, settings.admin_username)
    valid_password = secrets.compare_digest(payload.password, settings.admin_password)
    if not (valid_user and valid_password):
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos.")

    _clear_login_failures(request, payload.username)
    request.session.clear()
    request.session.update({
        "admin_authenticated": True,
        "admin_username": settings.admin_username,
        "csrf_token": secrets.token_urlsafe(32),
        "auth_time": datetime.now(timezone.utc).timestamp(),
    })
    return {"authenticated": True, "username": settings.admin_username, "redirect": "/admin"}


@login_router.get("/session")
def session_info(request: Request, username: str = Depends(require_admin_session)) -> dict:
    return {
        "authenticated": True,
        "username": username,
        "csrf_token": request.session.get("csrf_token"),
    }


@login_router.post("/logout", dependencies=[Depends(require_admin_access)])
def logout(request: Request) -> dict:
    request.session.clear()
    return {"authenticated": False, "message": "Sessão encerrada."}


@admin_router.get("/api-keys")
def list_api_keys() -> dict:
    with SessionLocal() as db:
        items = db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc(), ApiKey.id.desc())).all()
        now = datetime.now(timezone.utc)
        result = []
        for key in items:
            expires = key.expires_at
            if expires and expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            result.append({
                "id": key.id,
                "name": key.name,
                "key_prefix": key.key_prefix,
                "scopes": key.scopes or [],
                "created_by": key.created_by,
                "created_at": key.created_at.isoformat() if key.created_at else None,
                "expires_at": key.expires_at.isoformat() if key.expires_at else None,
                "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
                "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
                "expired": bool(expires and expires <= now),
                "active": key.revoked_at is None and not (expires and expires <= now),
            })
        return {"items": result, "total": len(result)}


@admin_router.post("/api-keys", status_code=status.HTTP_201_CREATED)
def create_api_key(payload: ApiKeyCreateRequest, username: str = Depends(require_admin_session)) -> dict:
    token = "caepi_live_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=payload.expires_in_days) if payload.expires_in_days else None
    prefix = token[:20]
    with SessionLocal() as db:
        item = ApiKey(
            name=payload.name.strip(),
            key_prefix=prefix,
            key_hash=digest,
            scopes=list(payload.scopes),
            created_by=username,
            expires_at=expires_at,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return {
            "id": item.id,
            "name": item.name,
            "key_prefix": item.key_prefix,
            "scopes": item.scopes,
            "created_at": item.created_at.isoformat() if item.created_at else now.isoformat(),
            "expires_at": item.expires_at.isoformat() if item.expires_at else None,
            "api_key": token,
            "warning": "Copie agora. O segredo completo não será exibido novamente.",
        }


@admin_router.delete("/api-keys/{key_id}")
def revoke_api_key(key_id: int) -> dict:
    with SessionLocal() as db:
        item = db.get(ApiKey, key_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Chave de API não encontrada.")
        if item.revoked_at is None:
            item.revoked_at = datetime.now(timezone.utc)
            db.commit()
        return {"id": item.id, "revoked": True, "revoked_at": item.revoked_at.isoformat()}


@admin_router.get("/certificates/{ca_number}")
def admin_get_certificate(ca_number: str) -> dict:
    normalized = "".join(ch for ch in ca_number if ch.isdigit())
    if not normalized or len(normalized) > 32:
        raise HTTPException(status_code=422, detail="Informe um número de CA válido.")
    with SessionLocal() as db:
        certificate = db.scalar(select(Certificate).where(Certificate.ca_number == normalized))
        if certificate is None:
            raise HTTPException(status_code=404, detail=f"CA {normalized} não encontrado na base local.")
        return {"certificate": _serialize_certificate(db, certificate), "source": "local_database", "scraping_started": False}
