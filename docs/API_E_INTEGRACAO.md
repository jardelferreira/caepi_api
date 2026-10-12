# API e integração com sistemas externos

## Princípios

- Consumidores externos usam uma chave Bearer criada em `/admin/api-keys`.
- Os segredos completos são exibidos uma única vez e o banco armazena o hash SHA-256.
- Cada chave possui escopos, prazo e revogação independente.
- A API de integração só lê o PostgreSQL e, no endpoint de PDF, busca o objeto existente no armazenamento. Nenhum desses endpoints inicia scraping.
- Não exponha a chave em JavaScript público, repositório, logs ou documentação compartilhada.

## Endpoints externos

### Consultar certificado

```http
GET /api/v1/certificates/{ca_number}
Authorization: Bearer caepi_live_SUA_CHAVE
Accept: application/json
```

Escopo: `certificates:read`.

Exemplo:

```bash
export CAEPI_API_KEY='COLE_A_CHAVE_DO_ADMIN'
curl -sS \
  -H "Authorization: Bearer $CAEPI_API_KEY" \
  -H 'Accept: application/json' \
  http://localhost:8080/api/v1/certificates/15272
```

Resposta ilustrativa:

```json
{
  "certificate": {
    "ca_number": "15272",
    "status": "VÁLIDO",
    "valid_until": "2030-01-31",
    "manufacturer": "Fabricante exemplo",
    "equipment_name": "Equipamento exemplo",
    "brand": "Marca exemplo",
    "last_source": "caepi_portal",
    "last_source_update": "2026-10-12T10:30:00+00:00",
    "sources": {},
    "data": {},
    "documents": {
      "certificate_pdf_available": true,
      "certificate_pdf_sha256": "...",
      "certificate_pdf_size_bytes": 156572,
      "certificate_pdf_last_checked_at": "2026-10-12T10:30:00+00:00"
    }
  },
  "source": "local_database",
  "scraping_started": false
}
```

O exemplo ilustra a estrutura; valores, datas, campos e disponibilidade real dependem do cadastro local. `data` contém os dados consolidados adicionais sem o bloco interno `_meta`.

### Baixar PDF existente

```http
GET /api/v1/certificates/{ca_number}/pdf
Authorization: Bearer CHAVE_COM_DOCUMENTS_READ
```

Escopo: `documents:read`.

```bash
curl -fS -L \
  -H "Authorization: Bearer $CAEPI_API_KEY" \
  http://localhost:8080/api/v1/certificates/15272/pdf \
  -o CA-15272.pdf
```

A chamada retorna o PDF armazenado; ela não aciona o portal nem baixa um novo PDF. Se não existir documento local com estado `downloaded`, retorna `404`.

## Exemplo Python

Instale `requests` no ambiente consumidor (`pip install requests`) e use:

```python
import os
import requests

BASE_URL = os.getenv("CAEPI_API_URL", "http://localhost:8080")
API_KEY = os.environ["CAEPI_API_KEY"]


def consultar_ca(numero: str) -> dict:
    response = requests.get(
        f"{BASE_URL}/api/v1/certificates/{numero}",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json",
        },
        timeout=15,
    )
    if response.status_code == 404:
        raise LookupError(f"CA {numero} não cadastrado na base local")
    if response.status_code == 401:
        raise PermissionError("Chave ausente, inválida, expirada ou revogada")
    response.raise_for_status()
    payload = response.json()
    return payload["certificate"]


if __name__ == "__main__":
    certificate = consultar_ca("15272")
    print(certificate["ca_number"], certificate["status"], certificate["valid_until"])
```

Para sistemas executados em outro host, `localhost` precisa ser substituído pelo hostname/IP de API acessível; em produção use HTTPS e controle de rede. Não embuta o segredo num frontend.

## Login e gestão de chaves

- Página: `/admin/api-keys`.
- API administrativa de listar: `GET /api/v1/admin/api-keys`.
- Criar: `POST /api/v1/admin/api-keys` com nome, escopos e validade em dias.
- Revogar: `DELETE /api/v1/admin/api-keys/{key_id}`.
- Rotas administrativas exigem sessão válida e operações mutáveis exigem `X-CSRF-Token`.

Escopos aceitos atualmente: `certificates:read` e `documents:read`.

## Endpoints administrativos operacionais

Todas as seguintes rotas requerem login administrativo (cookie de sessão) e proteção CSRF em métodos mutáveis:

- `GET /api/v1/certificates/catalog` — catálogo pesquisável e filtros.
- `GET /api/v1/certificates/integrity/summary` — resumo de alertas.
- `GET /api/v1/certificates/campaigns/preview` — prévia sem enfileiramento.
- `POST /api/v1/certificates/campaigns` — criar campanha confirmada.
- `GET /api/v1/certificates/campaigns` — lista campanhas.
- `GET /api/v1/certificates/campaigns/{campaign_id}` — progresso/itens.
- `GET /api/v1/certificates/campaigns/{campaign_id}/report.csv` — relatório CSV.
- `POST /api/v1/certificates/campaigns/{campaign_id}/pause` — pausar.
- `POST /api/v1/certificates/campaigns/{campaign_id}/resume` — retomar.
- `POST /api/v1/certificates/campaigns/{campaign_id}/cancel` — cancelar.
- `POST /api/v1/certificates/campaigns/{campaign_id}/retry-failed` — criar recuperação para falhas.
- `POST /api/v1/certificates/batch` — lote pequeno explícito (até 10 CAs únicos).
- `POST /api/v1/certificates/{ca_number}/refresh` — atualizar por scraping.
- `POST /api/v1/certificates/{ca_number}/pdf` — enfileirar obtenção/atualização de PDF.
- `GET /api/v1/certificates/tasks/{task_id}` — consultar estado Celery.
- `GET /api/v1/tasks` e `/api/v1/tasks/summary` — histórico Redis.

## Importações administrativas

As rotas de importação exigem sessão e validação de origem/CSRF. Consulte `ADMINISTRACAO_E_BASES.md`. Os caminhos legados podem requerer uma `IMPORT_API_KEY` de compatibilidade para determinadas rotas; prefira a área administrativa autenticada disponibilizada pela versão atual.

## Códigos HTTP comuns

| Código | Significado para o consumidor |
|---|---|
| `200` | Consulta concluída / metadados retornados |
| `202` | Tarefa administrativa aceita para execução assíncrona |
| `201` | Chave de API criada; segredo aparece uma única vez |
| `401` | Chave ausente, inválida, expirada ou revogada; ou sessão ausente |
| `403` | Escopo insuficiente, CSRF inválido ou origem recusada |
| `404` | CA, PDF ou chave não encontrado |
| `413` | Upload acima do limite configurado |
| `415` | Tipo de arquivo não aceito |
| `422` | Entrada inválida, arquivo inválido ou regra de simulação não atendida |
| `429` | Excesso de tentativas de login |
| `502` | Falha upstream no acesso a portal, fonte remota ou object storage |
| `503` | Login/dependência/configuração necessária indisponível |

## Rate limit e contrato

A limitação de tentativas de login é aplicada no Redis (limite atual: 10 tentativas na janela de 15 minutos). Não há garantia documentada de rate limit por chave para as rotas de leitura externas nesta versão. Se a API for exposta a terceiros em produção, estabeleça quotas por cliente no gateway e monitore o uso.
