"""Contratos e estruturas comuns dos scrapers."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass(slots=True)
class ScrapedCertificate:
    ca_number: str
    fields: dict[str, Any] = field(default_factory=dict)
    source_url: str | None = None
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    warnings: list[str] = field(default_factory=list)


class CertificateScraper(Protocol):
    def fetch_certificate(self, ca_number: str) -> ScrapedCertificate | None:
        ...

