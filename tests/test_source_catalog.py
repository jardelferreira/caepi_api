from __future__ import annotations

from app.importers.source_catalog import _date_from_name, inspect_bytes, list_sources


def test_inspection_counts_rows_unique_cas_and_history_rows():
    data = (
        "#NRRegistroCA|DataValidade|Situacao|NRProcesso\n"
        "498|31/12/2028|VÁLIDO|P1\n"
        "498|31/12/2030|CANCELADO|P2\n"
        "601|31/12/2029|VÁLIDO|P3\n"
    ).encode("utf-8")
    report = inspect_bytes(data, source_info={"source_id": "manual_upload", "filename": "sample.txt"})
    assert report["rows"] == 3
    assert report["unique_ca_count"] == 2
    assert report["repeated_ca_rows"] == 1
    assert report["invalid_rows"] == 0
    assert report["delimiter"] == "|"


def test_inspection_rejects_unrecognized_headers():
    try:
        inspect_bytes(b"foo|bar\nx|y\n", source_info={"filename": "bad.txt"})
    except ValueError as exc:
        assert "reconhecer a coluna" in str(exc)
    else:
        raise AssertionError("Esperava rejeitar cabeçalho inválido")


def test_csv_source_date_is_extracted_from_published_filename():
    assert _date_from_name("base-de-dados-do-CAEPI-19052025.csv") == "2025-05-19"


def test_sources_are_manual_only_and_include_both_official_feeds():
    result = list_sources()
    assert result["automatic_import"] is False
    source_ids = {source["id"] for source in result["sources"]}
    assert source_ids == {"mte_ftp_txt", "mte_web_csv"}


def test_inspection_skips_metadata_preamble_before_csv_header():
    data = (
        'Base de dados CAEPI, atualizado em 18/05/2025 21:00\n'
        'NRRegistroCA;DataValidade;Situacao\n'
        '498;31/12/2028;VÁLIDO\n'
        '601;31/12/2029;VENCIDO\n'
    ).encode('utf-8')
    report = inspect_bytes(data, source_info={'source_id': 'manual_upload', 'filename': 'mte.csv'})
    assert report['header_row'] == 2
    assert report['header_fields'][0] == 'NRRegistroCA'
    assert report['rows'] == 2
    assert report['unique_ca_count'] == 2
    assert report['invalid_rows'] == 0


def test_streaming_importer_uses_the_same_preamble_detection():
    from io import BytesIO
    from app.importers.base import iter_delimited_rows

    data = (
        'Base de dados CAEPI, atualizado em 18/05/2025 21:00\n'
        'NRRegistroCA;DataValidade;Situacao\n'
        '498;31/12/2028;VÁLIDO\n'
    ).encode('utf-8')
    headers, iterator = iter_delimited_rows(BytesIO(data))
    rows = list(iterator)
    assert headers[0] == 'NRRegistroCA'
    assert len(rows) == 1
    assert rows[0][1]['NRRegistroCA'] == '498'
