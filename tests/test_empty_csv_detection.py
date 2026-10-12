import io
from pathlib import Path

import pytest

from app.importers.base import iter_delimited_rows
from app.importers.source_catalog import inspect_bytes


EMPTY_EXPORT = (('""' + (',""' * 18) + '\n') * 50).encode('ascii')
VALID_CSV = (
    'NRRegistroCA,DataValidade,Situacao\n'
    '498,31/12/2027,VÁLIDO\n'
    '42049,30/06/2028,VÁLIDO\n'
).encode('utf-8')


def test_inspection_explains_all_empty_csv_instead_of_generic_missing_header():
    with pytest.raises(ValueError, match='apenas campos vazios') as exc:
        inspect_bytes(EMPTY_EXPORT, source_info={'filename': 'base-de-dados-do-CAEPI-19052025.csv'})
    assert 'Nenhum dado foi importado' in str(exc.value)


def test_import_parser_rejects_all_empty_csv():
    with pytest.raises(ValueError, match='apenas campos vazios'):
        iter_delimited_rows(io.BytesIO(EMPTY_EXPORT))


def test_valid_csv_remains_inspectable():
    report = inspect_bytes(VALID_CSV, source_info={'filename': 'base.csv'})
    assert report['unique_ca_count'] == 2
    assert report['valid_ca_rows'] == 2


def test_actual_uploaded_file_is_recognized_as_empty_export():
    path = Path('/mnt/data/base-de-dados-do-CAEPI-19052025.csv')
    if not path.exists():
        pytest.skip('Arquivo anexado não montado no ambiente de teste')
    content = path.read_bytes()
    with pytest.raises(ValueError, match='apenas campos vazios'):
        inspect_bytes(content, source_info={'filename': path.name})
