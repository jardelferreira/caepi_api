"""Operações de objetos independentes do provedor S3."""

from typing import Mapping

from botocore.exceptions import ClientError


class ObjectStore:
    """Armazena objetos em um bucket S3 compatível."""

    def __init__(self, client, bucket: str):
        if not bucket.strip():
            raise ValueError("O nome do bucket não pode ser vazio.")
        self.client = client
        self.bucket = bucket

    def put_bytes(
        self,
        key: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        self._validate_key(key)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
            Metadata=dict(metadata or {}),
        )

    def get_bytes(self, key: str) -> bytes:
        self._validate_key(key)
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def head_object(self, key: str) -> dict:
        self._validate_key(key)
        return self.client.head_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.head_object(key)
            return True
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {"404", "NoSuchKey", "NotFound"} or status == 404:
                return False
            raise

    def delete_object(self, key: str) -> None:
        self._validate_key(key)
        self.client.delete_object(Bucket=self.bucket, Key=key)

    @staticmethod
    def _validate_key(key: str) -> None:
        if not key or key.startswith("/") or any(part == ".." for part in key.split("/")):
            raise ValueError("A chave do objeto é inválida.")
