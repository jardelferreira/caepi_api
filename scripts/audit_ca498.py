#!/usr/bin/env python3
"""Auditoria somente de leitura do CA, documentos e observações."""
import hashlib
import os
import sys
from collections import Counter

try:
    from sqlalchemy import select, func
    from app.core.database import SessionLocal
    from app.models.ca import Certificate
    from app.models.document import CertificateDocument
    from app.models.observation import CertificateObservation
    from app.storage import get_object_store
except Exception as exc:
    print(f"ERRO ao importar a aplicação: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(2)

CA_NUMBER = os.environ.get("CA_NUMBER", "498")
EXPECTED_KEY = os.environ.get(
    "EXPECTED_KEY",
    "certificates/498/certificate-94f041d880d2c758.pdf",
)
EXPECTED_SHA = os.environ.get(
    "EXPECTED_SHA256",
    "94f041d880d2c758340636751f7c0c64503d66a3f2e1b30270afed85ec5eebe4",
)
EXPECTED_SIZE = 155864


def main():
    issues = 0
    print(f"Auditoria somente de leitura — CA {CA_NUMBER}")
    print("Nenhum registro ou objeto será criado, atualizado ou removido.\n")

    with SessionLocal() as session:
        certificate = session.scalar(
            select(Certificate).where(Certificate.ca_number == CA_NUMBER)
        )
        if certificate is None:
            print("FAIL | Certificado não encontrado.")
            return 1

        print(f"PASS | Certificado encontrado: id={certificate.id}, CA={certificate.ca_number}")

        docs = session.scalars(
            select(CertificateDocument).where(
                CertificateDocument.certificate_id == certificate.id
            )
        ).all()
        observations = session.scalars(
            select(CertificateObservation).where(
                CertificateObservation.certificate_id == certificate.id
            )
        ).all()

        print(f"\nDocumentos associados: {len(docs)}")
        if not docs:
            print("FAIL | Não há documentos associados.")
            issues += 1

        # Reporta todos os documentos sem assumir que cada versão seja duplicata.
        duplicate_fingerprints = Counter(
            (getattr(doc, "sha256", None), getattr(doc, "object_key", None))
            for doc in docs
            if getattr(doc, "sha256", None) or getattr(doc, "object_key", None)
        )
        for doc in docs:
            attrs = {col.key: getattr(doc, col.key, None) for col in doc.__table__.columns}
            print(
                f"  Documento id={attrs.get('id')}, tipo={attrs.get('document_type')}, "
                f"status={attrs.get('status')}, key={attrs.get('object_key')}, "
                f"sha256={attrs.get('sha256')}, size={attrs.get('size_bytes')}"
            )

        exact_key_docs = [d for d in docs if getattr(d, "object_key", None) == EXPECTED_KEY]
        if not exact_key_docs:
            print("\nFAIL | A chave esperada não está associada ao certificado.")
            issues += 1
        else:
            print(f"\nPASS | Chave esperada associada a {len(exact_key_docs)} registro(s).")
            exact_sha_docs = [
                d for d in exact_key_docs
                if getattr(d, "sha256", None) == EXPECTED_SHA
                and getattr(d, "size_bytes", None) == EXPECTED_SIZE
            ]
            if exact_sha_docs:
                print("PASS | Pelo menos um registro tem SHA-256 e tamanho esperados.")
            else:
                print("FAIL | Metadados não correspondem ao SHA-256/tamanho esperado.")
                issues += 1

        # O objetivo não é afirmar que várias versões são inválidas:
        # sinaliza apenas documentos com a mesma chave/hash repetidos no banco.
        key_counts = Counter(getattr(d, "object_key", None) for d in docs if getattr(d, "object_key", None))
        sha_counts = Counter(getattr(d, "sha256", None) for d in docs if getattr(d, "sha256", None))
        repeated_keys = {k: n for k, n in key_counts.items() if n > 1}
        repeated_shas = {k: n for k, n in sha_counts.items() if n > 1}
        if repeated_keys:
            print(f"ALERTA | object_key repetida em vários registros: {repeated_keys}")
        else:
            print("PASS | Nenhuma object_key repetida entre os documentos do certificado.")
        if repeated_shas:
            print(f"INFO | SHA-256 repetido em registros: {repeated_shas} (avaliar se são versões legítimas).")
        else:
            print("PASS | Nenhum SHA-256 repetido entre os documentos do certificado.")

        print(f"\nObservações históricas associadas: {len(observations)}")
        if observations:
            times = [
                getattr(o, "observed_at", None)
                or getattr(o, "created_at", None)
                for o in observations
            ]
            times = [t for t in times if t is not None]
            if times:
                print(f"  Mais antiga: {min(times)}")
                print(f"  Mais recente: {max(times)}")
            print("PASS | Observações históricas preservadas; nenhuma alteração realizada.")
        else:
            print("INFO | Nenhuma observação associada ao certificado.")

    print("\nVerificação do objeto no SeaweedFS:")
    try:
        store = get_object_store()
        content = store.get_bytes(EXPECTED_KEY)
        if not isinstance(content, (bytes, bytearray)):
            print(f"FAIL | Tipo retornado inesperado: {type(content).__name__}")
            issues += 1
        else:
            digest = hashlib.sha256(content).hexdigest()
            size = len(content)
            if digest == EXPECTED_SHA:
                print("PASS | SHA-256 do objeto corresponde ao esperado.")
            else:
                print(f"FAIL | SHA-256 divergente: {digest}")
                issues += 1
            if size == EXPECTED_SIZE:
                print("PASS | Tamanho do objeto corresponde ao esperado.")
            else:
                print(f"FAIL | Tamanho divergente: {size}")
                issues += 1
            if content.startswith(b"%PDF-"):
                print("PASS | Assinatura PDF presente.")
            else:
                print("FAIL | Assinatura PDF ausente.")
                issues += 1
    except Exception as exc:
        print(f"FAIL | Leitura do objeto falhou: {type(exc).__name__}: {exc}")
        issues += 1

    print()
    if issues:
        print(f"Auditoria finalizada com {issues} divergência(s). Nenhum dado foi alterado.")
        return 1
    print("Auditoria finalizada sem divergências críticas. Nenhum dado foi alterado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
