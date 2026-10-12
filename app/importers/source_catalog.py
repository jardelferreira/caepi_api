"""Descoberta e inspeção manual das fontes oficiais de dados CAEPI.

A descoberta nunca importa registros. Apenas lista fontes candidatas e, quando solicitada,
baixa/analisa o arquivo escolhido pelo usuário para obter data, tamanho, contagem e hash.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import re
import zipfile
from datetime import datetime, timezone
from ftplib import FTP
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen, urljoin
from html.parser import HTMLParser

from app.importers.base import detect_layout

FTP_HOST = "ftp.mtps.gov.br"
FTP_DIR = "portal/fiscalizacao/seguranca-e-saude-no-trabalho/caepi/"
FTP_ZIP = "tgg_export_caepi.zip"
FTP_TXT = "tgg_export_caepi.txt"
MTE_CSV_URL = "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/seguranca-e-saude-no-trabalho/equipamentos-de-protecao-individual-epi"
MAX_BYTES = 250 * 1024 * 1024
TIMEOUT = 45


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _date_from_name(name: str) -> str | None:
    match = re.search(r"(\d{2})[.-](\d{2})[.-](\d{4})", name)
    if match:
        d, m, y = match.groups()
        return f"{y}-{m}-{d}"
    match = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})", name)
    if match:
        return "-".join(match.groups())
    match = re.search(r"(?<!\d)(\d{2})(\d{2})(\d{4})(?!\d)", name)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month}-{day}"
    return None


def list_sources() -> dict[str, Any]:
    """Return known source choices without downloading/importing any data."""
    return {
        "status": "available",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "automatic_import": False,
        "sources": [
            {
                "id": "mte_ftp_txt",
                "label": "TXT oficial via FTP (ZIP → TXT)",
                "transport": f"ftp://{FTP_HOST}/{FTP_DIR}{FTP_ZIP}",
                "format": "TXT delimitado por | dentro de ZIP",
                "date": None,
                "date_note": "A listagem FTP não garante data de referência; a data será inferida dos metadados e conteúdo disponível.",
            },
            {
                "id": "mte_web_csv",
                "label": "CSV publicado na página do MTE",
                "transport": MTE_CSV_URL,
                "format": "CSV/TXT delimitado com cabeçalho",
                "date": _date_from_name("base-de-dados-do-CAEPI-19052025.csv"),
                "date_note": "O sistema localiza o link CSV na página oficial e verifica os metadados ao inspecionar.",
            },
        ],
    }


def _download_ftp_txt() -> tuple[bytes, dict[str, Any]]:
    buff = io.BytesIO()
    with FTP(FTP_HOST, timeout=TIMEOUT) as ftp:
        ftp.login()
        ftp.cwd(FTP_DIR)
        try:
            response = ftp.sendcmd(f"MDTM {FTP_ZIP}")
            modified_raw = response[4:].strip() if response.startswith("213 ") else None
        except Exception:
            modified_raw = None
        try:
            size = ftp.size(FTP_ZIP)
        except Exception:
            size = None
        if size and size > MAX_BYTES:
            raise ValueError("O ZIP do FTP excede o limite de 250 MiB.")
        ftp.retrbinary(f"RETR {FTP_ZIP}", buff.write, blocksize=1024 * 128)
    archive_data = buff.getvalue()
    if not archive_data:
        raise ValueError("O servidor FTP retornou um ZIP vazio.")
    if len(archive_data) > MAX_BYTES:
        raise ValueError("O ZIP do FTP excede o limite de 250 MiB.")
    try:
        with zipfile.ZipFile(io.BytesIO(archive_data)) as zf:
            candidates = [n for n in zf.namelist() if Path(n).name.lower() == FTP_TXT.lower()]
            if not candidates:
                candidates = [n for n in zf.namelist() if n.lower().endswith(".txt")]
            if not candidates:
                raise ValueError("O ZIP do FTP não contém arquivo TXT.")
            txt_info = zf.getinfo(candidates[0])
            if txt_info.file_size > MAX_BYTES:
                raise ValueError("O TXT dentro do ZIP excede o limite de 250 MiB.")
            txt = zf.read(candidates[0])
    except zipfile.BadZipFile as exc:
        raise ValueError("O arquivo recebido do FTP não é um ZIP válido.") from exc
    source_modified = None
    if modified_raw and re.match(r"^\d{14}$", modified_raw):
        source_modified = f"{modified_raw[:4]}-{modified_raw[4:6]}-{modified_raw[6:8]}T{modified_raw[8:10]}:{modified_raw[10:12]}:{modified_raw[12:14]}+00:00"
    return txt, {
        "source_id": "mte_ftp_txt",
        "filename": FTP_TXT,
        "archive_filename": FTP_ZIP,
        "source_modified": source_modified,
        "reported_date": (f"{modified_raw[:4]}-{modified_raw[4:6]}-{modified_raw[6:8]}" if modified_raw and len(modified_raw) >= 8 else None),
        "archive_size_bytes": len(archive_data),
        "archive_sha256": _sha256(archive_data),
    }


class _MteCsvLinkParser(HTMLParser):
    """Encontra o recurso CSV pelo texto/nome publicado, sem fixar o caminho interno do CMS."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []


def _download_web_csv(url: str | None = None) -> tuple[bytes, dict[str, Any]]:
    if url:
        raise ValueError("A URL do CSV não pode ser informada livremente; use a fonte oficial detectada na página do MTE.")
    page_request = Request(MTE_CSV_URL, headers={"User-Agent": "CAEPI-API/0.1 (manual source inspection)"})
    try:
        with urlopen(page_request, timeout=TIMEOUT) as response:
            page_url = response.geturl()
            if not page_url.startswith("https://www.gov.br/"):
                raise ValueError("A página foi redirecionada para um domínio não reconhecido do MTE.")
            page_data = response.read(8 * 1024 * 1024 + 1)
            if len(page_data) > 8 * 1024 * 1024:
                raise ValueError("A página oficial excedeu o limite de leitura esperado.")
            page_encoding = _encoding(page_data)
            page = page_data.decode(page_encoding, errors="replace")
            page_last_modified = response.headers.get("Last-Modified")
        parser = _MteCsvLinkParser()
        parser.feed(page)
        candidates: list[tuple[int, str, str]] = []
        for href, label in parser.links:
            joined = urljoin(page_url, html.unescape(href))
            hint = (joined + " " + label).lower()
            if ".csv" in hint and ("caepi" in hint or "certificado" in hint):
                score = 2 if "base-de-dados-do-caepi" in hint else 1
                candidates.append((score, joined, label))
        if not candidates:
            raise RuntimeError("A página oficial do MTE não apresentou link CSV CAEPI reconhecível. Você pode enviar o CSV manualmente pela dashboard.")
        candidates.sort(key=lambda item: item[0], reverse=True)
        download_page_url = candidates[0][1]
        path_name = download_page_url.rstrip("/").split("/")[-1]
        if path_name.lower() == "view" and len(download_page_url.rstrip("/").split("/")) >= 2:
            path_name = download_page_url.rstrip("/").split("/")[-2]
        filename = path_name or "base-do-CAEPI.csv"
        # Plone/Gov.br pages often expose a /view resource; prefer the explicit file endpoint.
        attempts = [download_page_url]
        if download_page_url.rstrip("/").endswith("/view"):
            attempts.insert(0, download_page_url.rsplit("/view", 1)[0] + "/@@download/file")
        elif not download_page_url.endswith("/@@download/file"):
            attempts.insert(0, download_page_url.rstrip("/") + "/@@download/file")
        data = b""
        final_url = download_page_url
        last_modified = page_last_modified
        filename = Path(filename).name or "base-do-CAEPI.csv"
        content_type = ""
        last_error: Exception | None = None
        for candidate_url in attempts:
            try:
                request = Request(candidate_url, headers={"User-Agent": "CAEPI-API/0.1 (manual source inspection)"})
                with urlopen(request, timeout=TIMEOUT) as response:
                    final_url = response.geturl()
                    if not final_url.startswith("https://www.gov.br/"):
                        raise ValueError("O download foi redirecionado para domínio fora do gov.br.")
                    data = response.read(MAX_BYTES + 1)
                    if len(data) > MAX_BYTES:
                        raise ValueError("O CSV excede o limite de 250 MiB.")
                    content_type = response.headers.get("Content-Type", "")
                    last_modified = response.headers.get("Last-Modified") or last_modified
                    content_disp = response.headers.get("Content-Disposition", "")
                    match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^;"\']+)', content_disp, re.I)
                    if match:
                        filename = match.group(1).strip()
                    prefix = data.lstrip()[:512].lower()
                    if b"<html" in prefix or b"<!doctype html" in prefix:
                        raise ValueError("O link retornou HTML em vez do CSV.")
                    break
            except Exception as exc:
                last_error = exc
                data = b""
        if not data:
            if last_error:
                raise RuntimeError(f"Não foi possível baixar o CSV oficial: {type(last_error).__name__}.") from last_error
            raise RuntimeError("Não foi possível baixar o CSV oficial.")
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Não foi possível consultar a página CSV do MTE: {type(exc).__name__}.") from exc
    return data, {
        "source_id": "mte_web_csv",
        "filename": filename,
        "download_url": final_url,
        "page_last_modified": page_last_modified,
        "http_last_modified": last_modified,
        "content_type": content_type,
        "size_bytes": len(data),
        "sha256": _sha256(data),
    }


def _encoding(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            data[:65536].decode(enc)
            return enc
        except UnicodeDecodeError:
            pass
    return "utf-8-sig"


def _delimiter(sample: str) -> str:
    delimiter, header, _ = detect_layout(sample)
    if header:
        return delimiter
    try:
        return csv.Sniffer().sniff(sample[:65536], delimiters=",;|\t").delimiter
    except csv.Error:
        line = next((ln for ln in sample.splitlines() if ln.strip()), "")
        return max((",", ";", "|", "\t"), key=lambda sep: line.count(sep))


def inspect_bytes(data: bytes, *, source_info: dict[str, Any]) -> dict[str, Any]:
    if not data:
        raise ValueError("A fonte retornou um arquivo vazio.")
    if len(data) > MAX_BYTES:
        raise ValueError("Arquivo excede o limite de 250 MiB.")
    encoding = _encoding(data)
    decoded = data.decode(encoding, errors="replace")
    # O CSV anexado ao projeto em 12/10/2026 continha 122.580 linhas com 19
    # campos vazios e nenhum caractere alfanumérico. Detecta esse tipo de
    # exportação defeituosa antes de tentar adivinhar cabeçalho/delimitador.
    if not any(char.isalnum() for char in decoded):
        line_count = len(decoded.splitlines())
        filename = Path(str(source_info.get("filename") or "arquivo enviado")).name
        raise ValueError(
            f"O arquivo '{filename}' contém apenas campos vazios ({line_count:,} linhas aparentes); "
            "não há cabeçalho nem números de CA. Nenhum dado foi importado. "
            "O arquivo baixado pode estar defeituoso; tente baixá-lo novamente ou selecione a base TXT via FTP."
        )
    sample = decoded[:256 * 1024]
    delim, header, header_row_index = detect_layout(sample)
    if not header:
        raise ValueError(
            "Não foi possível reconhecer a coluna do número do CA no cabeçalho. "
            "O arquivo pode conter metadados antes do cabeçalho ou usar nomes de coluna diferentes."
        )
    reader = csv.reader(io.StringIO(decoded, newline=""), delimiter=delim, skipinitialspace=True)
    for _ in range(header_row_index + 1):
        next(reader, None)
    rows = 0
    unique_cas: set[str] = set()
    valid_ca_rows = 0
    invalid_rows = 0
    ca_index = next((i for i, value in enumerate(header) if _is_ca_header(value)), None)
    # detect_layout only accepts a header containing a recognized CA number field.
    if ca_index is None:
        raise ValueError("Não foi possível reconhecer a coluna do número do CA no cabeçalho.")
    for row in reader:
        if not row or not any(str(x or "").strip() for x in row):
            continue
        rows += 1
        if len(row) != len(header) or ca_index >= len(row):
            invalid_rows += 1
            continue
        raw_ca = re.sub(r"\.0+$", "", str(row[ca_index] or "").strip())
        ca = re.sub(r"\D", "", raw_ca)
        if ca:
            valid_ca_rows += 1
            unique_cas.add(ca)
        else:
            invalid_rows += 1
    reported_date = source_info.get("reported_date") or _date_from_name(str(source_info.get("filename", "")))
    if not reported_date and source_info.get("http_last_modified"):
        try:
            from email.utils import parsedate_to_datetime
            reported_date = parsedate_to_datetime(source_info["http_last_modified"]).date().isoformat()
        except (TypeError, ValueError, OverflowError):
            reported_date = None
    return {
        **source_info,
        "inspected_at": datetime.now(timezone.utc).isoformat(),
        "size_bytes": source_info.get("size_bytes", len(data)),
        "sha256": source_info.get("sha256", _sha256(data)),
        "format": "CSV" if delim != "|" else "TXT delimitado por |",
        "delimiter": "TAB" if delim == "\\t" else delim,
        "encoding": encoding,
        "header_columns": len(header),
        "header_row": header_row_index + 1,
        "header_fields": header,
        "rows": rows,
        "unique_ca_count": len(unique_cas),
        "valid_ca_rows": valid_ca_rows,
        "repeated_ca_rows": max(0, valid_ca_rows - len(unique_cas)),
        "invalid_rows": invalid_rows,
        "reported_date": reported_date,
        "automatic_import": False,
    }


def _is_ca_header(value: object) -> bool:
    """Delegates the CA-number column rules to the importer/header detector."""
    from app.importers.base import ca_number_header_score
    return ca_number_header_score(value) > 0


def inspect_source(source_id: str) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    if source_id == "mte_ftp_txt":
        data, info = _download_ftp_txt()
    elif source_id == "mte_web_csv":
        data, info = _download_web_csv()
    else:
        raise ValueError("Fonte desconhecida. Escolha FTP/TXT ou CSV do MTE.")
    info["size_bytes"] = len(data)
    info["sha256"] = _sha256(data)
    report = inspect_bytes(data, source_info=info)
    # Staging file is not imported. Caller can explicitly confirm import later.
    return data, info, report


def inspect_local_upload(data: bytes, filename: str) -> dict[str, Any]:
    return inspect_bytes(data, source_info={
        "source_id": "manual_upload",
        "filename": Path(filename).name,
        "size_bytes": len(data),
        "sha256": _sha256(data),
        "reported_date": _date_from_name(filename),
    })
