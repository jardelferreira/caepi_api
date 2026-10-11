from app.scrapers.caepi_portal import CaepiPortalScraper, FIELD_IDS
from app.workers.tasks import _parse_date


def test_normalize_ca_number():
    assert CaepiPortalScraper._normalize_ca("CA 42.049") == "42049"


def test_reject_empty_ca_number():
    try:
        CaepiPortalScraper._normalize_ca("---")
    except ValueError as exc:
        assert "válido" in str(exc)
    else:
        raise AssertionError("Era esperado ValueError")


def test_legacy_fields_are_mapped():
    assert FIELD_IDS["numero_ca"] == "lblNRRegistroCA"
    assert FIELD_IDS["data_validade"] == "lblDTValidade"
    assert "historico_alteracoes" in FIELD_IDS


def test_selector_matches_aspnet_suffix():
    assert CaepiPortalScraper._selector("txtNumeroCA", "input") == 'input[id$="txtNumeroCA"]'


def test_parse_brazilian_date():
    assert _parse_date("04/02/2027 00:00:00") is not None
    assert _parse_date("04/02/2027 00:00:00").isoformat() == "2027-02-04"


def test_parse_unknown_date_returns_none():
    assert _parse_date("sem validade") is None
