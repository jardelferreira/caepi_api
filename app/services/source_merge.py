"""Regras compartilhadas de precedência entre fontes de dados de certificados."""

from __future__ import annotations

from typing import Any

SOURCE_PRIORITY: dict[str, int] = {
    "legacy_preserved": 15,
    "mte_official_base": 10,
    "caepi_portal": 20,
}


def has_value(value: Any) -> bool:
    """Retorna False para valores ausentes comuns em CSV e scraping."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def effective_existing_source(
    field_source: str | None,
    last_source: str | None,
) -> str:
    """Infere a origem de registros legados sem metadados por campo.

    Dados não vazios de origem desconhecida são preservados durante a importação
    oficial, mas podem ser atualizados por uma consulta do portal.
    """
    if field_source in SOURCE_PRIORITY:
        return field_source
    if last_source in SOURCE_PRIORITY:
        return str(last_source)
    return "legacy_preserved"


def should_replace(
    current_value: Any,
    current_source: str | None,
    incoming_value: Any,
    incoming_source: str,
) -> bool:
    """Decide se o valor recebido pode atualizar o consolidado.

    Valores vazios nunca apagam dados. A mesma fonte pode atualizar seu próprio
    dado; entre fontes diferentes, somente uma fonte com prioridade maior vence.
    """
    if not has_value(incoming_value):
        return False
    if not has_value(current_value):
        return True
    if not current_source:
        current_source = "legacy_preserved"
    if current_source == incoming_source:
        return True
    return SOURCE_PRIORITY.get(incoming_source, 0) > SOURCE_PRIORITY.get(current_source, 0)
