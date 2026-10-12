"""Área temporária de preparação de bases escolhidas manualmente pelo operador."""
from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

STAGING_DIR = Path("/tmp/caepi-import-staging")
TTL_SECONDS = 30 * 60
TOKEN_RE = re.compile(r"^[a-f0-9]{64}$")


def _cleanup() -> None:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - TTL_SECONDS
    for meta_path in STAGING_DIR.glob("*.json"):
        try:
            if meta_path.stat().st_mtime < cutoff:
                token = meta_path.stem
                meta_path.unlink(missing_ok=True)
                (STAGING_DIR / f"{token}.bin").unlink(missing_ok=True)
        except OSError:
            continue


def create_staging(data: bytes, *, filename: str, report: dict[str, Any]) -> dict[str, Any]:
    _cleanup()
    token = secrets.token_hex(32)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    data_path = STAGING_DIR / f"{token}.bin"
    meta_path = STAGING_DIR / f"{token}.json"
    # Write data first and only publish metadata after the staged bytes are durable.
    data_path.write_bytes(data)
    metadata = {
        "token": token,
        "filename": Path(filename).name,
        "created_at_epoch": time.time(),
        "report": report,
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    return {"staging_token": token, "expires_in_seconds": TTL_SECONDS}


def read_staging(token: str) -> tuple[Path, dict[str, Any]]:
    if not TOKEN_RE.fullmatch(token or ""):
        raise ValueError("Identificador temporário inválido.")
    _cleanup()
    meta_path = STAGING_DIR / f"{token}.json"
    data_path = STAGING_DIR / f"{token}.bin"
    if not meta_path.is_file() or not data_path.is_file():
        raise ValueError("A base preparada expirou ou não foi encontrada. Inspecione a fonte novamente.")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    if time.time() - float(metadata.get("created_at_epoch", 0)) > TTL_SECONDS:
        meta_path.unlink(missing_ok=True)
        data_path.unlink(missing_ok=True)
        raise ValueError("A base preparada expirou. Inspecione a fonte novamente.")
    return data_path, metadata


def record_simulation(token: str, result: dict[str, Any]) -> None:
    """Persist the latest successful preview so a write cannot skip the review step."""
    _path, metadata = read_staging(token)
    metadata["simulation"] = {
        "created_at_epoch": time.time(),
        "source_sha256": (metadata.get("report") or {}).get("sha256"),
        "rows_accepted": int(result.get("rows_accepted", 0)),
        "rows_rejected": int(result.get("rows_rejected", 0)),
        "unique_cas_in_file": int(result.get("unique_cas_in_file", 0)),
    }
    (STAGING_DIR / f"{token}.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )


def validate_simulation(metadata: dict[str, Any]) -> None:
    simulation = metadata.get("simulation")
    if not isinstance(simulation, dict):
        raise ValueError("Execute a simulação na dashboard antes de confirmar a importação.")
    if time.time() - float(simulation.get("created_at_epoch", 0)) > TTL_SECONDS:
        raise ValueError("A simulação expirou. Execute a simulação novamente antes de importar.")
    if not int(simulation.get("rows_accepted", 0)):
        raise ValueError("A simulação não encontrou linhas válidas para importar.")
    if simulation.get("source_sha256") != (metadata.get("report") or {}).get("sha256"):
        raise ValueError("A fonte preparada mudou desde a simulação. Inspecione novamente.")


def remove_staging(token: str) -> None:
    if TOKEN_RE.fullmatch(token or ""):
        (STAGING_DIR / f"{token}.json").unlink(missing_ok=True)
        (STAGING_DIR / f"{token}.bin").unlink(missing_ok=True)
