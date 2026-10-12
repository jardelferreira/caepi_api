"""Endpoints administrativos para importação da base oficial do CAEPI."""

from __future__ import annotations

import csv
import hashlib
import secrets
from pathlib import PurePath

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status

from app.importers.official_base import import_official_base

router = APIRouter(prefix="/imports", tags=["imports"])
MAX_IMPORT_BYTES = 250 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


@router.post("/official-base", status_code=status.HTTP_200_OK)
async def upload_official_base(
    request: Request,
    file: UploadFile = File(
        ..., description="CSV ou TXT delimitado exportado da base oficial do MTE"
    ),
    dry_run: bool = Query(True, description="Valida e simula sem gravar no banco quando true"),
) -> dict:
    """Valida ou importa a base oficial, sempre protegida por uma chave de administração."""
    try:
        _authorize_import(request)

        filename = PurePath(file.filename or "").name
        if not filename or PurePath(filename).suffix.lower() not in {".csv", ".txt"}:
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                                detail="Envie um arquivo .csv ou .txt delimitado.")

        await file.seek(0)
        size = 0
        digest = hashlib.sha256()
        while chunk := await file.read(CHUNK_SIZE):
            size += len(chunk)
            if size > MAX_IMPORT_BYTES:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                    detail="Arquivo excede o limite de 250 MiB.")
            digest.update(chunk)
        await file.seek(0)
        return import_official_base(
            file.file,
            filename=filename,
            file_sha256=digest.hexdigest(),
            dry_run=dry_run,
        )
    except HTTPException:
        raise
    except (ValueError, UnicodeError, csv.Error) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc)) from exc
    finally:
        await file.close()



@router.get("/sources")
def get_import_sources(
    request: Request,
) -> dict:
    """Lista fontes conhecidas sem baixar nem importar a base."""
    _authorize_import(request)
    from app.importers.source_catalog import list_sources
    return list_sources()


@router.post("/inspect-source")
def inspect_remote_source(
    request: Request,
    source_id: str = Query(..., pattern="^(mte_ftp_txt|mte_web_csv)$"),
) -> dict:
    """Baixa e conta uma fonte selecionada pelo usuário, mas não persiste certificados."""
    _authorize_import(request)
    from app.importers.source_catalog import inspect_source
    try:
        data, info, report = inspect_source(source_id)
        from app.importers.staging import create_staging
        staged = create_staging(data, filename=str(info.get("filename") or "base.txt"), report=report)
        return {"status": "inspected", "automatic_import": False, "source": report, **staged}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Falha ao consultar a fonte externa: {type(exc).__name__}.") from exc


@router.post("/inspect-upload")
async def inspect_uploaded_source(
    request: Request,
    file: UploadFile = File(...),
) -> dict:
    """Analisa CSV/TXT enviado pelo operador; não grava a base CAEPI."""
    _authorize_import(request)
    from app.importers.source_catalog import inspect_local_upload
    try:
        filename = PurePath(file.filename or "").name
        if not filename or PurePath(filename).suffix.lower() not in {".csv", ".txt"}:
            raise HTTPException(status_code=415, detail="Envie um arquivo .csv ou .txt.")
        data = await file.read(MAX_IMPORT_BYTES + 1)
        if len(data) > MAX_IMPORT_BYTES:
            raise HTTPException(status_code=413, detail="Arquivo excede o limite de 250 MiB.")
        try:
            report = inspect_local_upload(data, filename)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        from app.importers.staging import create_staging
        staged = create_staging(data, filename=filename, report=report)
        return {"status": "inspected", "automatic_import": False, "source": report, **staged}
    finally:
        await file.close()


def _authorize_import(request: Request) -> None:
    # Importações são exclusivamente administrativas. A sessão é verificada aqui e
    # novamente como dependência de router, junto do token CSRF em métodos mutáveis.
    if request.session.get("admin_authenticated"):
        return
    raise HTTPException(status_code=403, detail="Autenticação administrativa necessária.")

@router.post("/import-staged")
def import_staged_source(
    request: Request,
    staging_token: str = Query(..., min_length=64, max_length=64),
    dry_run: bool = Query(True, description="Simular sem gravar por padrão"),
) -> dict:
    """Valida/importa uma fonte preparada. A gravação exige dry_run=false explícito."""
    _authorize_import(request)
    from app.importers.official_base import import_official_base
    from app.importers.staging import read_staging, remove_staging, record_simulation, validate_simulation
    try:
        staged_path, metadata = read_staging(staging_token)
        import hashlib
        with staged_path.open("rb") as digest_stream:
            digest = hashlib.file_digest(digest_stream, "sha256").hexdigest()
        expected_digest = (metadata.get("report") or {}).get("sha256")
        if expected_digest and not secrets.compare_digest(digest, expected_digest):
            raise ValueError("O arquivo temporário mudou após a inspeção. Inspecione a fonte novamente.")
        if not dry_run:
            validate_simulation(metadata)
        with staged_path.open("rb") as stream:
            result = import_official_base(
                stream,
                filename=str(metadata.get("filename") or "base.txt"),
                file_sha256=digest,
                dry_run=dry_run,
            )
        if dry_run:
            record_simulation(staging_token, result)
        else:
            remove_staging(staging_token)
        return {**result, "staging_token": None if not dry_run else staging_token,
                "automatic_import": False}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
