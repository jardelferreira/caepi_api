"""Utilitários para importação de bases oficiais delimitadas."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Iterator


@dataclass(slots=True)
class ImportReport:
    source: str
    started_at: datetime
    finished_at: datetime | None = None
    rows_read: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    errors_truncated: bool = False


def normalize_header(value: object) -> str:
    """Normaliza cabeçalhos em português sem depender de bibliotecas externas."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", text.lower())


def detect_encoding(sample: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "utf-8-sig"


CA_NUMBER_HEADER_ALIASES = {
    "nrregistroca", "registroca", "numeroca", "nrca", "ca", "canumber",
    "certificadoaprovacao", "numerocertificadoaprovacao", "numerodocertificadoaprovacao",
    "numerodocertificadodeaprovacao", "registroca", "numerodoregistroca",
    "numeroderegistroca", "numeroderegistrodoca", "registrodoca", "nrregistrodoca",
    "nrregistro", "nroca", "codigoca", "numerodoca", "nrdoca",
    "numerodocertificadodeaprovacaoca", "nrdocertificadodeaprovacao",
}
DELIMITERS = ("|", ";", ",", "\t")


def ca_number_header_score(value: object) -> int:
    """Retorna um score se o campo parece identificar o número do CA."""
    normalized = normalize_header(value)
    if normalized in CA_NUMBER_HEADER_ALIASES:
        return 100
    # Cobertura de variações nos arquivos públicos, por exemplo
    # "Número de registro do CA" e "NR do Certificado de Aprovação".
    has_ca = "ca" in normalized
    has_registration = "registro" in normalized or "registr" in normalized
    has_number = "numero" in normalized or normalized.startswith("nr") or "codigo" in normalized
    has_certificate = "certificado" in normalized and (
        "aprovacao" in normalized or has_ca
    )
    if has_ca and (has_registration or has_number):
        return 70
    if has_certificate:
        return 60
    return 0


def detect_layout(sample_text: str, *, max_header_rows: int = 80) -> tuple[str, list[str], int]:
    """Detecta delimitador e linha de cabeçalho, tolerando metadados antes do cabeçalho.

    Retorna (delimitador, cabeçalho, índice do registro do cabeçalho, base 0).
    A detecção é feita por nome de coluna do CA, não por suposição sobre a primeira linha.
    """
    candidates: list[tuple[int, int, int, str, list[str]]] = []
    for delimiter in DELIMITERS:
        try:
            reader = csv.reader(io.StringIO(sample_text, newline=""), delimiter=delimiter, skipinitialspace=True)
            for row_index, row in enumerate(reader):
                if row_index >= max_header_rows:
                    break
                cleaned = [str(cell or "").strip().lstrip("\ufeff") for cell in row]
                if not any(cleaned):
                    continue
                scores = [ca_number_header_score(cell) for cell in cleaned]
                best_score = max(scores, default=0)
                if best_score:
                    # Prioriza uma correspondência exata; em seguida, cabeçalho com
                    # mais colunas e finalmente a ocorrência mais próxima do início.
                    candidates.append((best_score, len(cleaned), -row_index, delimiter, cleaned))
                    break
        except csv.Error:
            continue
    if candidates:
        score, width, negative_index, delimiter, header = max(candidates, key=lambda c: (c[0], c[1], c[2]))
        return delimiter, header, -negative_index

    # Retorna uma hipótese de separador para produzir uma mensagem de validação clara.
    try:
        delimiter = csv.Sniffer().sniff(sample_text[:65536], delimiters=",;|\t").delimiter
    except csv.Error:
        first_line = next((line for line in sample_text.splitlines() if line.strip()), "")
        delimiter = max(DELIMITERS, key=lambda sep: first_line.count(sep))
    return delimiter, [], 0


def detect_delimiter(sample_text: str) -> str:
    """Detecta o separador; reconhece primeiro o cabeçalho quando está disponível."""
    delimiter, header, _ = detect_layout(sample_text)
    if header:
        return delimiter
    try:
        return csv.Sniffer().sniff(sample_text[:65536], delimiters=",;|\t").delimiter
    except csv.Error:
        first_line = next((line for line in sample_text.splitlines() if line.strip()), "")
        return max(DELIMITERS, key=lambda delim: first_line.count(delim))


def iter_delimited_rows(
    binary_file: BinaryIO,
) -> tuple[list[str], Iterator[tuple[int, dict[str, str]]]]:
    """Retorna cabeçalho e iterador de linhas, ignorando até 80 linhas de preâmbulo.

    Mantém o arquivo em streaming durante a importação. A leitura inicial é apenas uma
    amostra para localizar o cabeçalho real; as linhas anteriores não são consideradas dados.
    """
    binary_file.seek(0)
    sample = binary_file.read(256 * 1024)
    binary_file.seek(0)
    # Não deixa um CSV exportado como milhares de células vazias avançar até a
    # etapa de importação. A verificação percorre o arquivo em blocos e restaura
    # a posição original, sem carregar uma segunda cópia inteira na memória.
    has_alphanumeric = False
    while True:
        chunk = binary_file.read(1024 * 1024)
        if not chunk:
            break
        if any((48 <= value <= 57) or (65 <= value <= 90) or (97 <= value <= 122) or value >= 128 for value in chunk):
            has_alphanumeric = True
            break
    binary_file.seek(0)
    if not has_alphanumeric:
        raise ValueError(
            "O arquivo contém apenas campos vazios; não há cabeçalho nem números de CA. "
            "Nenhum dado foi importado. Baixe novamente o arquivo ou utilize a base TXT oficial via FTP."
        )
    encoding = detect_encoding(sample)
    sample_text = sample.decode(encoding, errors="replace")
    delimiter, headers, header_record_index = detect_layout(sample_text)
    if not headers:
        raise ValueError("Cabeçalho não reconhecido: não foi encontrada a coluna do número do CA.")

    text_stream = io.TextIOWrapper(binary_file, encoding=encoding, newline="")
    reader = csv.reader(text_stream, delimiter=delimiter, skipinitialspace=True)
    try:
        for _ in range(header_record_index + 1):
            next(reader, None)
    except csv.Error as exc:
        text_stream.detach()
        raise ValueError("Não foi possível ler o cabeçalho do arquivo delimitado.") from exc

    def rows() -> Iterator[tuple[int, dict[str, str]]]:
        try:
            for row_number, values in enumerate(reader, start=header_record_index + 2):
                if not values or not any(str(value or "").strip() for value in values):
                    continue
                normalized: dict[str, str] = {}
                for index, key in enumerate(headers):
                    normalized[str(key).strip().lstrip("\ufeff")] = (
                        str(values[index]).strip() if index < len(values) and values[index] is not None else ""
                    )
                if len(values) > len(headers):
                    normalized["__extra_columns__"] = str(values[len(headers):])
                yield row_number, normalized
        finally:
            try:
                text_stream.detach()
            except (ValueError, OSError):
                pass

    return headers, rows()


class OfficialBaseImporter:
    """Compatibilidade com o esboço anterior de validação de arquivos locais."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir

    def validate_archive(self, archive_path: Path) -> None:
        if not archive_path.exists() or archive_path.stat().st_size == 0:
            raise ValueError("Arquivo de origem inexistente ou vazio")
