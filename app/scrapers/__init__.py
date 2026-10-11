"""Integrações de scraping de fontes oficiais."""

from app.scrapers.caepi_portal import CaepiPortalScraper, ScrapedPdf
from app.scrapers.base import CertificateScraper, ScrapedCertificate

__all__ = ["CaepiPortalScraper", "ScrapedPdf", "CertificateScraper", "ScrapedCertificate"]
