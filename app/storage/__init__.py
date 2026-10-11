"""Abstração de armazenamento de objetos para SeaweedFS e Cloudflare R2."""

from app.core.config import Settings, settings
from app.storage.object_store import ObjectStore
from app.storage.s3_client import create_s3_client


def get_object_store(config: Settings | None = None) -> ObjectStore:
    """Cria o adaptador de armazenamento com as configurações atuais."""
    config = config or settings
    return ObjectStore(
        client=create_s3_client(config),
        bucket=config.s3_bucket,
    )


__all__ = ["ObjectStore", "get_object_store"]
