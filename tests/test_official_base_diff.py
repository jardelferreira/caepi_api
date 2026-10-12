from __future__ import annotations

from datetime import date
from io import BytesIO

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.importers.official_base import import_official_base
from app.models.ca import Certificate
from app.models.observation import CertificateObservation


HEADER = "NRRegistroCA|DataValidade|Situacao|RazaoSocial|NomeEquipamento\n"


def _factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, factory


def _import(factory, content: str, *, dry_run: bool = True):
    return import_official_base(
        BytesIO(content.encode("utf-8")),
        filename="tgg_export_caepi.txt",
        file_sha256="a" * 64,
        dry_run=dry_run,
        session_factory=factory,
    )


def test_incremental_diff_reports_field_changes_new_cas_and_missing_previous_cas():
    engine, factory = _factory()
    try:
        with factory() as db:
            db.add_all([
                Certificate(
                    ca_number="498",
                    status="VÁLIDO",
                    valid_until=date(2028, 12, 31),
                    manufacturer="Fabricante antigo",
                    equipment_name="Respirador antigo",
                    last_source="mte_official_base",
                    consolidated_data={
                        "official_base": {"NRRegistroCA": "498", "Situacao": "VÁLIDO"},
                        "_meta": {
                            "field_sources": {
                                "status": "mte_official_base",
                                "valid_until": "mte_official_base",
                                "manufacturer": "mte_official_base",
                                "equipment_name": "mte_official_base",
                            },
                            "sources": {"mte_official_base": {"source_file": "old.txt"}},
                        },
                    },
                ),
                Certificate(
                    ca_number="999",
                    status="VENCIDO",
                    last_source="mte_official_base",
                    consolidated_data={
                        "official_base": {"NRRegistroCA": "999", "Situacao": "VENCIDO"},
                        "_meta": {"sources": {"mte_official_base": {"source_file": "old.txt"}}},
                    },
                ),
            ])
            db.commit()

        report = _import(
            factory,
            HEADER
            + "498|31/12/2025|VENCIDO|Fabricante novo|Respirador novo\n"
            + "15272|31/12/2030|VÁLIDO|Fabricante novo|Capacete\n",
            dry_run=True,
        )

        diff = report["diff_summary"]
        assert report["status"] == "validated"
        assert report["inserted"] == 1
        assert report["rows_accepted"] == 2
        assert diff["previous_database_ca_count"] == 2
        assert diff["previous_official_ca_count"] == 2
        assert diff["new_ca_count"] == 1
        assert diff["cas_with_consolidated_field_changes"] == 1
        assert diff["consolidated_field_change_counts"] == {
            "status": 1,
            "valid_until": 1,
            "manufacturer": 1,
            "equipment_name": 1,
        }
        assert diff["previously_official_cas_missing_from_source_count"] == 1
        assert diff["previously_official_cas_missing_from_source_sample"] == ["999"]
        assert diff["missing_cas_are_preserved"] is True
        assert diff["field_change_sample"][0]["ca_number"] == "498"

        # A simulação deve ser somente leitura, inclusive para registros ausentes na fonte.
        with factory() as db:
            old_ca = db.scalar(select(Certificate).where(Certificate.ca_number == "498"))
            assert old_ca.status == "VÁLIDO"
            assert old_ca.valid_until == date(2028, 12, 31)
            assert db.scalar(select(Certificate).where(Certificate.ca_number == "15272")) is None
            assert db.scalar(select(func.count()).select_from(CertificateObservation)) == 0

        # Confirmar a importação incremental não apaga um CA ausente da nova fonte.
        imported = _import(
            factory,
            HEADER
            + "498|31/12/2025|VENCIDO|Fabricante novo|Respirador novo\n"
            + "15272|31/12/2030|VÁLIDO|Fabricante novo|Capacete\n",
            dry_run=False,
        )
        assert imported["status"] == "imported"
        with factory() as db:
            changed_ca = db.scalar(select(Certificate).where(Certificate.ca_number == "498"))
            missing_ca = db.scalar(select(Certificate).where(Certificate.ca_number == "999"))
            assert changed_ca.status == "VENCIDO"
            assert missing_ca is not None
    finally:
        engine.dispose()



def test_report_compares_source_hash_with_most_recent_import_metadata():
    engine, factory = _factory()
    try:
        with factory() as db:
            db.add(Certificate(
                ca_number="498",
                status="VÁLIDO",
                last_source="mte_official_base",
                consolidated_data={
                    "official_base": {"NRRegistroCA": "498", "Situacao": "VÁLIDO"},
                    "_meta": {"sources": {"mte_official_base": {
                        "source_file": "tgg_export_caepi.txt",
                        "sha256": "b" * 64,
                        "imported_at": "2026-07-15T21:00:00+00:00",
                    }}},
                },
            ))
            db.commit()
        report = _import(factory, HEADER + "498|31/12/2027|VÁLIDO|Fabricante|Botina\n", dry_run=True)
        diff = report["diff_summary"]
        assert diff["previous_imported_source"]["source_file"] == "tgg_export_caepi.txt"
        assert diff["previous_imported_source"]["sha256"] == "b" * 64
        assert diff["same_content_as_previous_import"] is False
        assert diff["content_differs_from_previous_import"] is True
    finally:
        engine.dispose()

def test_historical_duplicate_ca_rows_count_final_field_difference_only():
    engine, factory = _factory()
    try:
        with factory() as db:
            db.add(Certificate(
                ca_number="498",
                status="VÁLIDO",
                last_source="mte_official_base",
                consolidated_data={
                    "official_base": {"NRRegistroCA": "498", "Situacao": "VÁLIDO"},
                    "_meta": {"sources": {"mte_official_base": {"source_file": "old.txt"}},
                            "field_sources": {"status": "mte_official_base"}},
                },
            ))
            db.commit()
        report = _import(
            factory,
            HEADER
            + "498|31/12/2026|VENCIDO|Fabricante|Botina\n"
            + "498|31/12/2027|SUSPENSO|Fabricante|Botina\n",
            dry_run=True,
        )
        diff = report["diff_summary"]
        assert report["repeated_ca_rows"] == 1
        assert diff["cas_with_consolidated_field_changes"] == 1
        assert diff["consolidated_field_change_counts"]["status"] == 1
        assert diff["consolidated_field_change_counts"]["valid_until"] == 1
        with factory() as db:
            ca = db.scalar(select(Certificate).where(Certificate.ca_number == "498"))
            assert ca.status == "VÁLIDO"
    finally:
        engine.dispose()
