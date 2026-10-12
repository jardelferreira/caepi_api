"""Importação validada da base oficial CAEPI para a projeção consolidada."""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.importers.base import ImportReport, iter_delimited_rows, normalize_header
from app.models.ca import Certificate
from app.models.observation import CertificateObservation
from app.services.source_merge import has_value, should_replace

SOURCE = "mte_official_base"
ERROR_LIMIT = 100
DIFF_SAMPLE_LIMIT = 50
FIELD_LABELS: dict[str, str] = {
    "status": "Situação",
    "valid_until": "Validade",
    "manufacturer_document": "CNPJ/documento do fabricante",
    "manufacturer": "Fabricante",
    "equipment_name": "Equipamento",
    "equipment_description": "Descrição do equipamento",
    "brand": "Marca",
    "protection_details": "Detalhes de proteção",
}


def _display_diff_value(value: Any, limit: int = 180) -> str | None:
    if value is None:
        return None
    rendered = value.isoformat() if isinstance(value, date) else str(value)
    if len(rendered) > limit:
        return rendered[: limit - 1] + "…"
    return rendered


def _has_previous_official_snapshot(certificate: Certificate) -> bool:
    data = certificate.consolidated_data or {}
    if not isinstance(data, dict):
        return False
    meta = data.get("_meta") or {}
    sources = meta.get("sources") or {} if isinstance(meta, dict) else {}
    return bool(data.get("official_base")) or (isinstance(sources, dict) and SOURCE in sources)

ALIASES: dict[str, tuple[str, ...]] = {
    "ca_number": (
        "nrregistroca", "numerodoca", "numeroca", "nrca", "ca", "canumber",
        "certificadoaprovacao", "numerocertificadoaprovacao", "numerodocertificadoaprovacao",
        "numerodocertificadodeaprovacao", "registroca", "numerodoregistroca",
    ),
    "valid_until": (
        "datavalidade", "datadevalidade", "validade", "datafimvalidade", "validuntil",
    ),
    "status": ("situacao", "status", "situacaoca", "situacaodoca"),
    "manufacturer_document": (
        "cnpj", "cnpjfabricante", "cnpjdofabricante", "cnpjimportador",
        "documentofabricante", "documentodofabricanteimportador",
    ),
    "manufacturer": (
        "razaosocial", "fabricante", "razaosocialfabricante", "razaosocialimportador",
    ),
    "equipment_name": (
        "nomeequipamento", "nomedoequipamento", "equipamento", "nomeepi", "nomeproduto",
    ),
    "equipment_description": (
        "descricaoequipamento", "descricao", "descripcaoequipamento",
        "descricaodoequipamento", "descricaoepi",
    ),
    "brand": ("marca", "marcadoequipamento", "marcaepi"),
    "approved_for": ("aprovadoparalaudo", "aprovadopara", "finalidadeprotecao"),
    "restrictions": ("restricaolaudo", "restricoes", "restricaodeuso"),
    "analysis_observation": (
        "observacaoanaliselaudo", "observacaoanalise", "observacoes", "observacao",
    ),
}


def _lookup_headers(headers: list[str]) -> dict[str, str]:
    lookup = {normalize_header(header): header for header in headers if normalize_header(header)}
    result: dict[str, str] = {}
    for canonical, aliases in ALIASES.items():
        for alias in aliases:
            if alias in lookup:
                result[canonical] = lookup[alias]
                break
    return result


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _normalize_ca(value: Any) -> str:
    raw = str(value or "").strip()
    # Planilhas podem serializar números inteiros como "42049.0".
    raw = re.sub(r"\.0+$", "", raw)
    normalized = re.sub(r"\D", "", raw)
    if not normalized or len(normalized) > 32:
        raise ValueError("número de CA ausente ou inválido")
    return normalized


def _parse_date(value: Any) -> date | None:
    raw = _clean(value)
    if not raw:
        return None
    raw = raw.split(" ", maxsplit=1)[0].split("T", maxsplit=1)[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _row_value(row: dict[str, str], column: str | None) -> str | None:
    return _clean(row.get(column)) if column else None


def _map_row(row: dict[str, str], columns: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {
        "status": _row_value(row, columns.get("status")),
        "valid_until": _parse_date(_row_value(row, columns.get("valid_until"))),
        "manufacturer_document": _row_value(row, columns.get("manufacturer_document")),
        "manufacturer": _row_value(row, columns.get("manufacturer")),
        "equipment_name": _row_value(row, columns.get("equipment_name")),
        "equipment_description": _row_value(row, columns.get("equipment_description")),
        "brand": _row_value(row, columns.get("brand")),
    }
    protection_parts: list[str] = []
    for canonical, label in (
        ("approved_for", "Aprovado para"),
        ("restrictions", "Restrições"),
        ("analysis_observation", "Observações"),
    ):
        value = _row_value(row, columns.get(canonical))
        if value:
            protection_parts.append(f"{label}: {value}")
    values["protection_details"] = "\n".join(protection_parts) or None
    return values


def _is_empty(value: Any) -> bool:
    return not has_value(value)


def _metadata(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("_meta")
    if not isinstance(value, dict):
        value = {}
        data["_meta"] = value
    if not isinstance(value.get("field_sources"), dict):
        value["field_sources"] = {}
    if not isinstance(value.get("sources"), dict):
        value["sources"] = {}
    return value


def _field_source(
    meta: dict[str, Any], certificate: Certificate, attribute: str, current_value: Any
) -> str:
    mapped = meta["field_sources"].get(attribute)
    if mapped:
        return str(mapped)
    if certificate.last_source in {"mte_official_base", "caepi_portal"}:
        return str(certificate.last_source)
    return "legacy_preserved" if has_value(current_value) else ""


def import_official_base(
    binary_file: BinaryIO,
    *,
    filename: str,
    file_sha256: str,
    dry_run: bool = True,
    session_factory=SessionLocal,
) -> dict[str, Any]:
    """Valida e mescla uma base CSV/TXT delimitada sem apagar dados preexistentes.

    `dry_run=True` calcula o resultado esperado sem gravar alterações. A execução
    efetiva ocorre em uma única transação PostgreSQL.
    """
    started_at = datetime.now(timezone.utc)
    headers, row_iter = iter_delimited_rows(binary_file)
    columns = _lookup_headers(headers)
    if "ca_number" not in columns:
        raise ValueError(
            "Cabeçalho não reconhecido: esperava uma coluna como NRRegistroCA, "
            "Número do CA ou CA."
        )
    if len(headers) < 2:
        raise ValueError("Arquivo sem cabeçalho delimitado válido.")

    report = ImportReport(source=SOURCE, started_at=started_at)
    with session_factory() as db:
        certificates = {
            item.ca_number: item for item in db.scalars(select(Certificate)).all()
        }
        seen: set[str] = set()
        repeated_ca_rows = 0
        original_ca_numbers = set(certificates)
        previous_official_ca_numbers = {
            ca_number for ca_number, certificate in certificates.items()
            if _has_previous_official_snapshot(certificate)
        }
        previous_source_versions: Counter[tuple[str, str, str]] = Counter()
        for certificate in certificates.values():
            data = certificate.consolidated_data or {}
            meta = data.get("_meta") or {} if isinstance(data, dict) else {}
            sources = meta.get("sources") or {} if isinstance(meta, dict) else {}
            source_meta = sources.get(SOURCE) if isinstance(sources, dict) else None
            if isinstance(source_meta, dict) and source_meta.get("sha256"):
                previous_source_versions[(
                    str(source_meta.get("sha256") or ""),
                    str(source_meta.get("source_file") or ""),
                    str(source_meta.get("imported_at") or ""),
                )] += 1
        previous_version = None
        if previous_source_versions:
            (previous_sha, previous_filename, previous_imported_at), previous_version_records = (
                previous_source_versions.most_common(1)[0]
            )
            previous_version = {
                "sha256": previous_sha,
                "source_file": previous_filename or None,
                "imported_at": previous_imported_at or None,
                "certificate_records": previous_version_records,
            }
        encountered_existing: set[str] = set()
        changed_existing: set[str] = set()
        inserted_ca_numbers: set[str] = set()
        snapshot_changed_existing: set[str] = set()
        original_values_by_ca: dict[str, dict[str, Any]] = {}
        pending_observations: list[tuple[Certificate, str, Any, dict[str, Any]]] = []
        try:
            for line_number, raw_row in row_iter:
                report.rows_read += 1
                if "__extra_columns__" in raw_row:
                    _reject(report, line_number, "quantidade de colunas diferente do cabeçalho")
                    continue
                try:
                    ca_number = _normalize_ca(raw_row.get(columns["ca_number"]))
                except ValueError as exc:
                    _reject(report, line_number, str(exc))
                    continue
                if ca_number in seen:
                    # A base histórica TXT pode conter várias ocorrências legítimas do mesmo CA.
                    # Mantemos todas as linhas e deixamos a última ocorrência consolidar os campos.
                    repeated_ca_rows += 1
                seen.add(ca_number)
                report.rows_accepted += 1

                certificate = certificates.get(ca_number)
                is_new = certificate is None
                if is_new:
                    report.inserted += 1
                    inserted_ca_numbers.add(ca_number)
                    certificate = Certificate(ca_number=ca_number, consolidated_data={})
                    certificates[ca_number] = certificate
                    if not dry_run:
                        db.add(certificate)
                elif ca_number in original_ca_numbers:
                    encountered_existing.add(ca_number)

                assert certificate is not None
                before_changed = False
                data = copy.deepcopy(certificate.consolidated_data or {})
                meta = _metadata(data)
                field_sources = meta["field_sources"]
                mapped = _map_row(raw_row, columns)
                if not is_new and ca_number not in original_values_by_ca:
                    original_values_by_ca[ca_number] = {
                        attribute: copy.deepcopy(getattr(certificate, attribute))
                        for attribute in mapped
                    }

                for attribute, incoming_value in mapped.items():
                    current_value = getattr(certificate, attribute)
                    current_source = _field_source(meta, certificate, attribute, current_value)
                    if _is_empty(incoming_value):
                        # Não deixe que uma carga posterior mude a origem inferida só porque
                        # este arquivo não trouxe valor para o campo.
                        if has_value(current_value) and attribute not in field_sources:
                            field_sources[attribute] = current_source
                        continue
                    can_replace = should_replace(
                        current_value, current_source, incoming_value, SOURCE
                    )
                    if can_replace:
                        source_changed = current_source != SOURCE
                        value_changed = current_value != incoming_value
                        if source_changed or value_changed:
                            before_changed = True
                        field_sources[attribute] = SOURCE
                        if value_changed:
                            # Em dry_run, alteramos apenas o objeto ORM em memória para que
                            # ocorrências seguintes do mesmo CA simulem a sequência corretamente.
                            setattr(certificate, attribute, incoming_value)
                            if not dry_run:
                                pending_observations.append((
                                    certificate,
                                    attribute,
                                    incoming_value,
                                    {"value": incoming_value, "source_file": filename,
                                     "source_sha256": file_sha256},
                                ))
                    elif has_value(current_value) and attribute not in field_sources:
                        # Congela a origem legada inferida para que last_source futuro não
                        # altere a interpretação desse campo sem evidência.
                        field_sources[attribute] = current_source

                previous_snapshot = dict(data.get("official_base") or {})
                snapshot = dict(previous_snapshot)
                for key, value in raw_row.items():
                    key_clean = str(key).strip()
                    if key_clean and key_clean != "__extra_columns__":
                        # A fotografia da última ocorrência deve representar os campos vazios
                        # da fonte, mas esses vazios nunca limpam os campos consolidados.
                        snapshot[key_clean] = _clean(value) or ""
                snapshot_changed = snapshot != previous_snapshot
                if snapshot_changed:
                    before_changed = True
                    if not is_new and ca_number in original_ca_numbers:
                        snapshot_changed_existing.add(ca_number)
                data["official_base"] = snapshot
                new_source_meta = {
                    "imported_at": started_at.isoformat(),
                    "source_file": filename,
                    "sha256": file_sha256,
                }
                # Não alteramos ORM nem o relatório de mudanças efetivas no modo dry-run.
                if not dry_run:
                    meta["sources"][SOURCE] = new_source_meta
                    if before_changed or is_new:
                        certificate.last_source = SOURCE
                    certificate.last_source_update = started_at
                # Também manter no objeto em memória para simular múltiplas ocorrências
                # do mesmo CA; nenhuma dessas alterações é commitada no modo dry_run.
                certificate.consolidated_data = data

                if not is_new:
                    if before_changed:
                        report.updated += 1
                    else:
                        report.unchanged += 1
                if not is_new and ca_number in original_ca_numbers and before_changed:
                    changed_existing.add(ca_number)
                if not dry_run and len(pending_observations) >= 500:
                    db.flush()
                    _flush_observations(db, pending_observations, started_at)
                    pending_observations.clear()

            # Contadores por CA existente, não por linha histórica. Um CA repetido conta
            # apenas uma vez em updated/unchanged, enquanto rows_accepted mede as linhas.
            report.updated = len(changed_existing)
            report.unchanged = len(encountered_existing - changed_existing)

            # Diferenças reais nos campos consolidados, comparando o estado final simulado
            # com o valor que existia antes da importação. Linhas históricas repetidas não
            # são contadas várias vezes: é avaliado apenas o resultado final por CA.
            field_change_counts: dict[str, int] = {attribute: 0 for attribute in FIELD_LABELS}
            field_change_samples: list[dict[str, Any]] = []
            field_changed_ca_numbers: set[str] = set()
            for ca_number, previous_values in original_values_by_ca.items():
                certificate = certificates[ca_number]
                changes: list[dict[str, Any]] = []
                for attribute, previous_value in previous_values.items():
                    current_value = getattr(certificate, attribute)
                    if previous_value != current_value:
                        field_change_counts[attribute] = field_change_counts.get(attribute, 0) + 1
                        changes.append({
                            "field": attribute,
                            "label": FIELD_LABELS.get(attribute, attribute),
                            "before": _display_diff_value(previous_value),
                            "after": _display_diff_value(current_value),
                        })
                if changes:
                    field_changed_ca_numbers.add(ca_number)
                    if len(field_change_samples) < DIFF_SAMPLE_LIMIT:
                        field_change_samples.append({"ca_number": ca_number, "changes": changes})

            missing_from_latest_source = sorted(previous_official_ca_numbers - seen)
            diff_summary = {
                "previous_database_ca_count": len(original_ca_numbers),
                "previous_official_ca_count": len(previous_official_ca_numbers),
                "previous_imported_source": previous_version,
                "same_content_as_previous_import": bool(previous_version and previous_version.get("sha256") == file_sha256),
                "content_differs_from_previous_import": bool(previous_version and previous_version.get("sha256") != file_sha256),
                "unique_ca_count_in_source": len(seen),
                "new_ca_count": len(inserted_ca_numbers),
                "existing_cas_encountered": len(encountered_existing),
                "cas_with_official_snapshot_changes": len(snapshot_changed_existing),
                "cas_with_consolidated_field_changes": len(field_changed_ca_numbers),
                "consolidated_field_change_counts": {
                    key: value for key, value in field_change_counts.items() if value
                },
                "consolidated_field_labels": FIELD_LABELS,
                "previously_official_cas_missing_from_source_count": len(missing_from_latest_source),
                "previously_official_cas_missing_from_source_sample": missing_from_latest_source[:DIFF_SAMPLE_LIMIT],
                "new_ca_sample": sorted(inserted_ca_numbers)[:DIFF_SAMPLE_LIMIT],
                "field_change_sample": field_change_samples,
                "missing_cas_are_preserved": True,
                "automatic_import": False,
            }

            report.finished_at = datetime.now(timezone.utc)
            if report.rows_accepted == 0:
                raise ValueError("Nenhuma linha válida foi encontrada; importação cancelada.")
            if not dry_run:
                db.flush()
                _flush_observations(db, pending_observations, started_at)
                db.commit()
        except Exception:
            db.rollback()
            raise

    return {
        "status": "validated" if dry_run else "imported",
        "dry_run": dry_run,
        "source": SOURCE,
        "source_file": filename,
        "source_sha256": file_sha256,
        "started_at": started_at.isoformat(),
        "finished_at": (report.finished_at or datetime.now(timezone.utc)).isoformat(),
        "rows_read": report.rows_read,
        "rows_accepted": report.rows_accepted,
        "rows_rejected": report.rows_rejected,
        "unique_cas_in_file": len(seen),
        "repeated_ca_rows": repeated_ca_rows,
        "inserted": report.inserted,
        "updated": report.updated,
        "unchanged": report.unchanged,
        "diff_summary": diff_summary,
        "errors": report.errors,
        "errors_truncated": report.errors_truncated,
    }


def _reject(report: ImportReport, line_number: int, reason: str) -> None:
    report.rows_rejected += 1
    if len(report.errors) < ERROR_LIMIT:
        report.errors.append(f"Linha {line_number}: {reason}")
    else:
        report.errors_truncated = True


def _flush_observations(
    db: Session,
    pending: list[tuple[Certificate, str, Any, dict[str, Any]]],
    collected_at: datetime,
) -> None:
    for certificate, field_name, value, raw_payload in pending:
        value_text = value.isoformat() if isinstance(value, date) else str(value)
        json_safe_payload = json.loads(json.dumps(raw_payload, default=str, ensure_ascii=False))
        db.add(CertificateObservation(
            certificate_id=certificate.id,
            source=SOURCE,
            field_name=field_name[:100],
            value_text=value_text,
            raw_payload=json_safe_payload,
            collected_at=collected_at,
        ))
