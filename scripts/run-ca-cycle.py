#!/usr/bin/env python3
"""Executa um ciclo HTTP real de refresh e, opcionalmente, captura de PDF."""
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


def request_json(method: str, path: str):
    url = f"{BASE_URL}{path}"
    request = Request(url, method=method, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"body": body}
        return exc.code, payload
    except URLError as exc:
        raise RuntimeError(f"Não foi possível acessar {url}: {exc.reason}") from exc


def poll_task(task_id: str):
    safe_id = quote(task_id, safe="")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last_state = None
    while time.monotonic() < deadline:
        status, payload = request_json(
            "GET", f"/api/v1/certificates/tasks/{safe_id}"
        )
        if status != 200:
            raise RuntimeError(
                f"Consulta da tarefa retornou HTTP {status}: "
                f"{json.dumps(payload, ensure_ascii=False)}"
            )
        state = payload.get("state", "UNKNOWN")
        if state != last_state:
            print(f"  Estado da tarefa: {state}")
            last_state = state
        if state in {"SUCCESS", "FAILURE", "REVOKED"}:
            return payload
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(
        f"A tarefa {task_id} não terminou em {TIMEOUT_SECONDS}s. "
        "Consulte o status pela API e os logs do worker."
    )


def enqueue_and_wait(kind: str, ca_number: str):
    status, payload = request_json(
        "POST", f"/api/v1/certificates/{quote(ca_number, safe='')}/{kind}"
    )
    if status != 202:
        raise RuntimeError(
            f"Endpoint {kind} retornou HTTP {status}: "
            f"{json.dumps(payload, ensure_ascii=False)}"
        )
    task_id = payload.get("task_id")
    if not task_id:
        raise RuntimeError(f"Resposta sem task_id: {payload}")
    print(f"  Tarefa enfileirada: {task_id}")
    result = poll_task(task_id)
    print("  Resposta final:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("state") != "SUCCESS":
        raise RuntimeError(f"A tarefa terminou em estado {result.get('state')}.")
    return result.get("result") or {}


def main():
    print(f"API: {BASE_URL}")
    print("ATENÇÃO: este teste consulta o portal e pode atualizar o registro do CA no PostgreSQL.")
    print("Não apaga registros nem remove arquivos.")
    raw = input("Informe o número de CA que deseja testar: ").strip()
    ca_number = re.sub(r"\D", "", raw)
    if not ca_number or len(ca_number) > 32:
        print("Número de CA inválido.", file=sys.stderr)
        return 2

    confirm = input(
        f"Confirmar consulta real do CA {ca_number}? Digite SIM para continuar: "
    ).strip()
    if confirm != "SIM":
        print("Cancelado; nenhuma tarefa foi enviada.")
        return 0

    try:
        print("\n[1/2] Atualizando dados do CA...")
        refresh_result = enqueue_and_wait("refresh", ca_number)
        if refresh_result.get("status") == "not_found":
            print("O portal não retornou esse CA. A etapa de PDF foi cancelada.")
            return 1
        if refresh_result.get("status") not in {"ok", "unchanged", "downloaded"}:
            print("A tarefa terminou, mas o status retornado merece análise.")
            return 1

        answer = input("\nSolicitar também o PDF deste CA? (s/N): ").strip().lower()
        if answer != "s":
            print("Teste encerrado após a atualização dos dados.")
            return 0

        print("\n[2/2] Solicitando PDF...")
        pdf_result = enqueue_and_wait("pdf", ca_number)
        if pdf_result.get("status") in {"downloaded", "unchanged"}:
            print("\nPDF confirmado pela tarefa.")
            print(f"Chave de armazenamento: {pdf_result.get('document_key')}")
            print(f"SHA-256: {pdf_result.get('sha256')}")
            print(f"Tamanho: {pdf_result.get('size_bytes')} bytes")
            print("A validação do objeto no SeaweedFS e a conferência no banco podem ser feitas na etapa seguinte.")
            return 0
        print(f"Resultado do PDF não esperado: {pdf_result.get('status')}")
        return 1
    except (RuntimeError, TimeoutError) as exc:
        print(f"\nERRO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
