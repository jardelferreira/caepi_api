from __future__ import annotations

import urllib.error
from email.message import Message

from app.scrapers.caepi_portal import _cloudflare_challenge_body, _probe_cloudflare_challenge


def test_detects_cloudflare_challenge_html():
    assert _cloudflare_challenge_body(
        title="Just a moment...",
        source='<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>',
    )


def test_does_not_classify_plain_portal_html_as_challenge():
    assert not _cloudflare_challenge_body(
        title="Consulta CAEPI", source="<html><body>Pesquisar CA</body></html>"
    )


def test_probe_detects_cf_mitigated_challenge(monkeypatch):
    headers = Message()
    headers["Cf-Mitigated"] = "challenge"
    body = b"<html><title>Just a moment...</title></html>"

    # HTTPError's read() needs an fp, so inject a tiny file-like body as the error's fp.
    import io
    def fail_urlopen_with_body(_request, timeout=8):
        raise urllib.error.HTTPError(
            "https://caepi.trabalho.gov.br/internet/ConsultaCAInternet.aspx",
            403, "Forbidden", headers, io.BytesIO(body)
        )

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen_with_body)
    assert _probe_cloudflare_challenge(
        "https://caepi.trabalho.gov.br/internet/ConsultaCAInternet.aspx"
    )


def test_probe_returns_false_for_network_failure(monkeypatch):
    def fail_urlopen(_request, timeout=8):
        raise urllib.error.URLError("network unreachable")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    assert not _probe_cloudflare_challenge("https://caepi.trabalho.gov.br/")
