from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.api_key import ApiKey

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
api_key_bearer = HTTPBearer(auto_error=False, scheme_name="CAEPIApiKey")
ALLOWED_SCOPES = {"certificates:read", "documents:read"}


def admin_auth_is_configured() -> bool:
    return bool(
        settings.admin_username
        and settings.admin_password
        and settings.admin_session_secret
        and len(settings.admin_session_secret) >= 32
    )


def require_admin_session(request: Request) -> str:
    if not admin_auth_is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login administrativo desabilitado. Configure ADMIN_USERNAME, ADMIN_PASSWORD e ADMIN_SESSION_SECRET no .env.",
        )
    if not request.session.get("admin_authenticated"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação administrativa necessária.",
            headers={"WWW-Authenticate": "Session"},
        )
    auth_time = request.session.get("auth_time")
    if not isinstance(auth_time, (int, float)) or datetime.now(timezone.utc).timestamp() - auth_time > 8 * 60 * 60:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Sessão expirada. Entre novamente.")
    return str(request.session.get("admin_username") or settings.admin_username)


def require_admin_access(request: Request) -> str:
    """Protege APIs operacionais com sessão e exige CSRF em métodos mutáveis."""
    username = require_admin_session(request)
    if request.method.upper() not in SAFE_METHODS:
        expected = request.session.get("csrf_token")
        supplied = request.headers.get("X-CSRF-Token")
        if not expected or not supplied or not secrets.compare_digest(str(expected), supplied):
            raise HTTPException(status_code=403, detail="Token CSRF ausente ou inválido. Atualize a página e tente novamente.")
    return username


def _key_has_scope(api_key: ApiKey, required_scope: str) -> bool:
    scopes = api_key.scopes if isinstance(api_key.scopes, list) else []
    return required_scope in scopes


def _authenticate_api_key(credentials: HTTPAuthorizationCredentials | None, required_scope: str) -> ApiKey:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Informe uma chave de API no cabeçalho Authorization: Bearer <chave>.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raw_key = credentials.credentials.strip()
    if not raw_key or len(raw_key) > 256:
        raise HTTPException(status_code=401, detail="Chave de API inválida.")

    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        api_key = db.scalar(select(ApiKey).where(ApiKey.key_hash == digest))
        if api_key is None or api_key.revoked_at is not None:
            raise HTTPException(status_code=401, detail="Chave de API inválida ou revogada.")
        if api_key.expires_at is not None:
            expiration = api_key.expires_at
            if expiration.tzinfo is None:
                expiration = expiration.replace(tzinfo=timezone.utc)
            if expiration <= now:
                raise HTTPException(status_code=401, detail="Chave de API expirada.")
        if not _key_has_scope(api_key, required_scope):
            raise HTTPException(status_code=403, detail=f"A chave não possui o escopo {required_scope}.")
        api_key.last_used_at = now
        db.commit()
        # Retornar somente dados necessários, sem o hash.
        return ApiKey(
            id=api_key.id,
            name=api_key.name,
            key_prefix=api_key.key_prefix,
            key_hash="",
            scopes=list(api_key.scopes or []),
            created_by=api_key.created_by,
            created_at=api_key.created_at,
            expires_at=api_key.expires_at,
            last_used_at=now,
            revoked_at=api_key.revoked_at,
        )


def require_certificate_read(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(api_key_bearer)],
) -> ApiKey:
    return _authenticate_api_key(credentials, "certificates:read")


def require_document_read(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(api_key_bearer)],
) -> ApiKey:
    return _authenticate_api_key(credentials, "documents:read")
