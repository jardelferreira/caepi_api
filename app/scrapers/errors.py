"""Erros explícitos de acesso às fontes CAEPI."""


class PortalAccessBlockedError(RuntimeError):
    """O portal respondeu com desafio/bloqueio de acesso; não repetir automaticamente."""

