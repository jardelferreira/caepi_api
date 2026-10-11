#!/usr/bin/env python3
"""Confere o registro de documento no PostgreSQL e os bytes no SeaweedFS."""
import hashlib
import os
import sys

EXPECTED_CA = os.environ.get("CA_NUMBER", "498")
EXPECTED_SHA = os.environ.get(
    "EXPECTED_SHA256",
    "94f041d880d2c758340636751f7c0c64503d66a3f2e1b30270afed85ec5eebe4",
)
EXPECTED_KEY = os.environ.get(
    "EXPECTED_KEY",
    "certificates/498/certificate-94f041d880d2c758.pdf",
)
EXPECTED_SIZE = int(os.environ.get("EXPECTED_SIZE", "155864"))

try:
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.ca import Certificate
    from app.models.document import CertificateDocument
    from app.storage import get_object_store
except Exception as exc:
    print(f"ERRO ao importar os módulos da aplicação: {exc}", file=sys.stderr)
    raise SystemExit(2)


def fail(message):
    print(f"FAIL | {message}")
    return 1


def main():
    failures = 0
    print(f"Conferindo CA {EXPECTED_CA}")
    print(f"Chave esperada: {EXPECTED_KEY}")

    with SessionLocal() as session:
        certificate = session.scalar(
            select(Certificate).where(Certificate.ca_number == EXPECTED_CA)
        )
        if certificate is None:
            print("FAIL | CA não encontrado no PostgreSQL")
            return 1
        print(f"PASS | CA encontrado no PostgreSQL (id={certificate.id})")

        # Os nomes dos atributos de chave variam entre implementações;
        # listamos documentos vinculados ao certificado para inspeção segura.
        docs = session.scalars(
            select(CertificateDocument).where(
                CertificateDocument.certificate_id == certificate.id
            )
        ).all()
        matching = []
        for doc in docs:
            attrs = {
                col.key: getattr(doc, col.key, None)
                for col in doc.__table__.columns
            }
            key_values = [
                value for key, value in attrs.items()
                if isinstance(value, str) and value == EXPECTED_KEY
            ]
            if key_values:
                matching.append((doc, attrs))

        if not matching:
            print("FAIL | Nenhum registro documental contém a chave esperada")
            print("Colunas disponíveis em CertificateDocument:")
            print(", ".join(CertificateDocument.__table__.columns.keys()))
            print(f"Registros documentais vinculados ao CA: {len(docs)}")
            for doc in docs:
                attrs = {
                    col.key: getattr(doc, col.key, None)
                    for col in doc.__table__.columns
                }
                print(f"  Documento id={getattr(doc, 'id', '?')}: {attrs}")
            failures += 1
        else:
            doc, attrs = matching[0]
            print(f"PASS | Registro documental encontrado (id={attrs.get('id', '?')})")
            print("Metadados do registro:")
            for key, value in attrs.items():
                print(f"  {key}: {value}")

    try:
        store = get_object_store()
        content = store.get_bytes(EXPECTED_KEY)
        if not isinstance(content, (bytes, bytearray)):
            print(f"FAIL | get_bytes retornou tipo inesperado: {type(content).__name__}")
            failures += 1
        else:
            actual_sha = hashlib.sha256(content).hexdigest()
            actual_size = len(content)
            print(f"Tamanho encontrado: {actual_size} bytes")
            print(f"SHA-256 encontrado: {actual_sha}")
            if actual_size == EXPECTED_SIZE:
                print("PASS | Tamanho corresponde ao resultado da tarefa")
            else:
                print(f"FAIL | Tamanho esperado {EXPECTED_SIZE}, encontrado {actual_size}")
                failures += 1
            if actual_sha == EXPECTED_SHA:
                print("PASS | SHA-256 corresponde ao resultado da tarefa")
            else:
                print("FAIL | SHA-256 diverge do resultado da tarefa")
                failures += 1
            if content[:5] == b"%PDF-":
                print("PASS | Cabeçalho do objeto indica PDF")
            else:
                print("FAIL | Cabeçalho do objeto não parece ser PDF")
                failures += 1
    except Exception as exc:
        print(f"FAIL | Não foi possível ler o objeto do SeaweedFS: {type(exc).__name__}: {exc}")
        failures += 1

    print()
    if failures:
        print(f"Verificação concluída com {failures} falha(s). Nenhum dado foi alterado.")
        return 1
    print("Todas as verificações passaram. Nenhum dado foi alterado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
