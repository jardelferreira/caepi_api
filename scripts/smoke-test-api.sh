#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
TMP_BODY="$(mktemp)"
trap 'rm -f "$TMP_BODY"' EXIT

PASS=0
FAIL=0

check() {
  local label="$1"
  local method="$2"
  local path="$3"
  local expected="$4"
  local code

  code="$(curl -sS -o "$TMP_BODY" -w '%{http_code}' \
    -X "$method" "${BASE_URL}${path}")" || {
      echo "FAIL | ${label} | não foi possível conectar à API"
      FAIL=$((FAIL + 1))
      return
    }

  if [[ "$code" == "$expected" ]]; then
    echo "PASS | ${label} | HTTP ${code}"
    cat "$TMP_BODY"
    echo
    PASS=$((PASS + 1))
  else
    echo "FAIL | ${label} | esperado HTTP ${expected}, recebido HTTP ${code}"
    cat "$TMP_BODY"
    echo
    FAIL=$((FAIL + 1))
  fi
}

echo "Validando API em: ${BASE_URL}"
echo "Este roteiro não enfileira consultas válidas nem altera registros."
echo

check "Liveness" GET "/api/v1/health/live" "200"
check "Readiness/banco" GET "/api/v1/health/ready" "200"
check "Refresh rejeita CA inválido" POST "/api/v1/certificates/abc/refresh" "422"
check "PDF rejeita CA inválido" POST "/api/v1/certificates/abc/pdf" "422"
check "Consulta de tarefa desconhecida" GET "/api/v1/certificates/tasks/smoke-task-inexistente" "200"

echo
echo "Resultado: ${PASS} aprovados; ${FAIL} falhas."
if [[ "$FAIL" -ne 0 ]]; then
  exit 1
fi
