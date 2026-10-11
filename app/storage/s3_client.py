"""Criação de cliente S3 compatível com SeaweedFS e Cloudflare R2."""

import boto3
from botocore.config import Config

from app.core.config import Settings, settings


def create_s3_client(config: Settings | None = None):
    config = config or settings
    if not config.s3_endpoint_url:
        raise ValueError("S3_ENDPOINT_URL precisa estar configurado.")

    return boto3.client(
        "s3",
        endpoint_url=config.s3_endpoint_url,
        aws_access_key_id=config.s3_access_key_id,
        aws_secret_access_key=config.s3_secret_access_key,
        region_name=config.s3_region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )
