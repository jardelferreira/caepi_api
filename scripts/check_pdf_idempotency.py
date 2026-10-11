#!/usr/bin/env python3
"""Solicita novamente o PDF e reporta se a tarefa reconheceu conteúdo já existente."""
import json
import os
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")
POLL_INTERVAL = 2
TIMEOUT_SECONDS = 180


def call(method, path):
    req = Request(
        f"{BASE_URL}{path}",
        method=method,
        headers={"Accept": "application/json"},
    )
    try:
        with urlopen(req, timeout=15) as res:
            body = res.read().decode("utf-8", errors="replace")
            return res.status, json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"body": body}
        return exc.code, payload
    except URLError as exc:
        raise RuntimeError(f"Falha de conexão com {req.full_url}: {exc.reason}") from exc


def main():
    ca = os.environ.get("CA_NUMBER", "498")
    print(f"API: {BASE_URL}")
    print(f"CA selecionado: {ca}")
    print("Este teste envia uma nova tarefa real de PDF para o CA selecionado.")
    print("Não exclui registros nem remove objetos do armazenamento.")
    confirmation = input(f"Digite REPETIR-{ca} para confirmar: ").strip()
    if confirmation != f"REPETIR-{ca}":
        print("Cancelado; nenhuma tarefa foi enviada.")
        return 0

    status, payload = call(
        "POST", f"/api/v1/certificates/{quote(ca, safe='')}/pdf"
    )
    if status != 202:
        print(f"FAIL | Esperado HTTP 202, recebido {status}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    task_id = payload.get("task_id")
    if not task_id:
        print(f"FAIL | Resposta sem task_id: {payload}")
        return 1
    print(f"Tarefa enfileirada: {task_id}")

    deadline = time.monotonic() + TIMEOUT_SECONDS
    previous_state = None
    result = None
    while time.monotonic() < deadline:
        status, state_payload = call(
            "GET",
            f"/api/v1/certificates/tasks/{quote(str(task_id), safe='')}",
        )
        if status != 200:
            print(f"FAIL | Consulta de status HTTP {status}")
            print(json.dumps(state_payload, ensure_ascii=False, indent=2))
            return 1
        state = state_payload.get("state", "UNKNOWN")
        if state != previous_state:
            print(f"Estado: {state}")
            previous_state = state
        if state in {"SUCCESS", "FAILURE", "REVOKED"}:
            result = state_payload
            break
        time.sleep(POLL_INTERVAL)

    if result is None:
        print(f"TIMEOUT | Tarefa não terminou em {TIMEOUT_SECONDS}s.")
        print("Consulte os logs do worker antes de repetir.")
        return 2

    print("\nResposta final:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("state") != "SUCCESS":
        print("FAIL | A tarefa não terminou com SUCCESS.")
        return 1

    details = result.get("result") or {}
    print("\nResumo:")
    print(f"status: {details.get('status')}")
    print(f"deduplicated_by: {details.get('deduplicated_by')}")
    print(f"document_key: {details.get('document_key')}")
    print(f"sha256: {details.get('sha256')}")
    print(f"size_bytes: {details.get('size_bytes')}")

    if details.get("status") == "unchanged":
        print("\nPASS | O conteúdo foi reconhecido como já existente.")
        return 0
    if details.get("status") == "downloaded":
        print("\nATENÇÃO | A tarefa criou/registrou um download em vez de reconhecer conteúdo idêntico.")
        print("Não apague nada. Precisamos inspecionar a lógica e os registros.")
        return 3
    print("\nATENÇÃO | Status inesperado; preserve os registros e investigue.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
