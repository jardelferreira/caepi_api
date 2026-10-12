from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.importers.official_base import import_official_base
from app.models.ca import Certificate
from app.models.observation import CertificateObservation


HEADER = (
    "NRRegistroCA;DataValidade;Situacao;NRProcesso;CNPJ;RazaoSocial;"
    "NomeEquipamento;DescricaoEquipamento;AprovadoParaLaudo;RestricaoLaudo;"
    "ObservacaoAnaliseLaudo\n"
)


def _factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, factory


def _import(factory, content: str, *, dry_run: bool):
    return import_official_base(
        BytesIO(content.encode("utf-8")),
        filename="base-teste.csv",
        file_sha256="a" * 64,
        dry_run=dry_run,
        session_factory=factory,
    )


def test_dry_run_validates_without_writing_database():
    engine, factory = _factory()
    try:
        content = HEADER + (
            "498;31/12/2028;Válido;PROC-1;12.345.678/0001-90;Fabricante Exemplo;"
            "Respirador;Descrição do produto;Proteção respiratória;Nenhuma;Observação\n"
        )
        report = _import(factory, content, dry_run=True)
        assert report["status"] == "validated"
        assert report["rows_read"] == 1
        assert report["rows_accepted"] == 1
        assert report["inserted"] == 1
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(Certificate)) == 0
    finally:
        engine.dispose()


def test_import_inserts_mapped_fields_and_audits_changes():
    engine, factory = _factory()
    try:
        content = HEADER + (
            "498;31/12/2028;Válido;PROC-1;12.345.678/0001-90;Fabricante Exemplo;"
            "Respirador;Descrição do produto;Proteção respiratória;Nenhuma;Observação\n"
        )
        report = _import(factory, content, dry_run=False)
        assert report["status"] == "imported"
        assert report["inserted"] == 1
        with factory() as db:
            certificate = db.scalar(select(Certificate).where(Certificate.ca_number == "498"))
            assert certificate is not None
            assert certificate.status == "Válido"
            assert certificate.valid_until == date(2028, 12, 31)
            assert certificate.manufacturer_document == "12.345.678/0001-90"
            assert certificate.manufacturer == "Fabricante Exemplo"
            assert certificate.equipment_name == "Respirador"
            assert "Proteção respiratória" in certificate.protection_details
            assert certificate.consolidated_data["official_base"]["NRProcesso"] == "PROC-1"
            field_sources = certificate.consolidated_data["_meta"]["field_sources"]
            assert field_sources["status"] == "mte_official_base"
            assert db.scalar(select(func.count()).select_from(CertificateObservation)) >= 1
    finally:
        engine.dispose()


def test_reimport_is_idempotent_and_does_not_duplicate_observations():
    engine, factory = _factory()
    try:
        content = HEADER + (
            "498;31/12/2028;Válido;PROC-1;123;Fabricante;Respirador;Desc;Respiração;;\n"
        )
        first = _import(factory, content, dry_run=False)
        second = _import(factory, content, dry_run=False)
        assert first["inserted"] == 1
        assert second["inserted"] == 0
        assert second["updated"] == 0
        assert second["unchanged"] == 1
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(Certificate)) == 1
            assert db.scalar(select(func.count()).select_from(CertificateObservation)) == 7
    finally:
        engine.dispose()


def test_official_import_does_not_overwrite_portal_values():
    engine, factory = _factory()
    try:
        with factory() as db:
            db.add(Certificate(
                ca_number="498",
                status="VÁLIDO NO PORTAL",
                manufacturer="Fabricante do portal",
                last_source="caepi_portal",
                consolidated_data={"_meta": {"field_sources": {
                    "status": "caepi_portal", "manufacturer": "caepi_portal"
                }}},
            ))
            db.commit()
        content = HEADER + (
            "498;31/12/2028;CANCELADO;PROC-1;123;Fabricante CSV;Respirador;Desc;Respiração;;\n"
        )
        _import(factory, content, dry_run=False)
        with factory() as db:
            certificate = db.scalar(select(Certificate).where(Certificate.ca_number == "498"))
            assert certificate.status == "VÁLIDO NO PORTAL"
            assert certificate.manufacturer == "Fabricante do portal"
            assert certificate.consolidated_data["official_base"]["Situacao"] == "CANCELADO"
    finally:
        engine.dispose()


def test_invalid_header_and_empty_valid_data_are_rejected():
    engine, factory = _factory()
    try:
        with pytest.raises(ValueError, match="Cabeçalho não reconhecido"):
            _import(factory, "ColunaErrada;Descricao\nx;valor\n", dry_run=True)
        with pytest.raises(ValueError, match="Nenhuma linha válida"):
            _import(factory, HEADER + ";;;;;;;;;;\n", dry_run=True)
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(Certificate)) == 0
    finally:
        engine.dispose()


def test_pipe_delimited_txt_is_supported():
    engine, factory = _factory()
    try:
        content = "NRRegistroCA|Situacao|RazaoSocial\n498|Válido|Fabricante TXT\n"
        report = _import_pipe(factory, content)
        assert report["rows_accepted"] == 1
        with factory() as db:
            certificate = db.scalar(select(Certificate).where(Certificate.ca_number == "498"))
            assert certificate is not None
            assert certificate.status == "Válido"
            assert certificate.manufacturer == "Fabricante TXT"
    finally:
        engine.dispose()


def _import_pipe(factory, content: str):
    return import_official_base(
        BytesIO(content.encode("utf-8")),
        filename="base-teste.txt",
        file_sha256="c" * 64,
        dry_run=False,
        session_factory=factory,
    )


def test_repeated_ca_rows_are_treated_as_history_not_rejected():
    engine, factory = _factory()
    try:
        content = HEADER + (
            "498;31/12/2028;Válido;PROC-1;123;Fabricante;Respirador;Desc inicial;Respiração;;\n"
            "498;31/12/2030;CANCELADO;PROC-2;123;Fabricante;Respirador;Desc atualizada;Respiração;;\n"
        )
        preview = _import(factory, content, dry_run=True)
        assert preview["rows_read"] == 2
        assert preview["rows_accepted"] == 2
        assert preview["rows_rejected"] == 0
        assert preview["unique_cas_in_file"] == 1
        assert preview["repeated_ca_rows"] == 1
        assert preview["inserted"] == 1
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(Certificate)) == 0

        imported = _import(factory, content, dry_run=False)
        assert imported["inserted"] == 1
        assert imported["rows_accepted"] == 2
        assert imported["repeated_ca_rows"] == 1
        with factory() as db:
            certificate = db.scalar(select(Certificate).where(Certificate.ca_number == "498"))
            assert certificate is not None
            # A última ocorrência do CA é o estado consolidado, sem perder a auditoria.
            assert certificate.status == "CANCELADO"
            assert certificate.valid_until == date(2030, 12, 31)
            assert certificate.consolidated_data["official_base"]["NRProcesso"] == "PROC-2"
            observations = db.scalars(select(CertificateObservation).where(
                CertificateObservation.certificate_id == certificate.id
            )).all()
            assert len(observations) >= 1
    finally:
        engine.dispose()


def test_importer_skips_metadata_preamble_before_csv_header():
    engine, factory = _factory()
    try:
        content = (
            "Base de dados CAEPI, atualizado em 18/05/2025 21:00\n"
            + HEADER
            + "498;31/12/2028;Válido;PROC-1;123;Fabricante;Respirador;Desc;Respiração;;\n"
        )
        report = _import(factory, content, dry_run=True)
        assert report["rows_read"] == 1
        assert report["rows_accepted"] == 1
        assert report["inserted"] == 1
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(Certificate)) == 0
    finally:
        engine.dispose()
