# Instalação e primeiros testes

## Pré-requisitos

- Docker Engine e Docker Compose v2 funcionando no host.
- Portas `8080` e `8333` livres.
- Espaço em disco para PostgreSQL, Redis e arquivos PDF.
- Arquivo `.env` existente e protegido. Não substituir uma configuração válida pelo `.env.example`.

## Variáveis administrativas

Gere uma senha e segredo localmente:

```bash
openssl rand -hex 16
openssl rand -hex 32
```

Adicione ao `.env` existente:

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=VALOR_ALEATORIO_1
ADMIN_SESSION_SECRET=VALOR_ALEATORIO_2
ADMIN_COOKIE_SECURE=false
```

O segredo de sessão deve ter no mínimo 32 caracteres. `ADMIN_COOKIE_SECURE=false` é apenas para HTTP local; em HTTPS defina `true`. Configure também `POSTGRES_PASSWORD` e as credenciais do SeaweedFS de acordo com os volumes atuais. Não troque as credenciais de um banco/volume já inicializado sem um plano de migração.

Verifique a dependência para upload: `pyproject.toml` deve conter `python-multipart>=0.0.20,<1.0`. Ela é exigida pelo FastAPI para endpoints que usam `UploadFile`/`File`.

## Construir e iniciar

Na raiz do projeto:

```bash
docker compose config -q
docker compose up -d --build
docker compose ps
```

A API executa as migrações Alembic durante a inicialização. Não use `docker compose down -v`, pois isso remove os volumes persistentes.

## Smoke test

```bash
curl -i http://localhost:8080/api/v1/health/live
curl -i http://localhost:8080/api/v1/health/ready
docker compose exec -T api python -c "import multipart; print('python-multipart OK')"
```

O endpoint `/live` verifica que o processo responde. `/ready` verifica a conexão com o PostgreSQL. Se houver `502 Bad Gateway`, veja os logs da API antes de reiniciar Nginx:

```bash
docker compose logs --tail=100 api
docker compose logs --tail=50 nginx
```

## Login inicial

Abra `http://localhost:8080/login` e use as credenciais definidas no `.env`. Páginas do admin:

- `/admin`: início e navegação.
- `/admin/consult`: pesquisa local de um CA.
- `/admin/api-keys`: chaves externas.
- `/certificates`: catálogo, integridade e campanhas.
- `/dashboard`: monitor de tarefas e gerenciamento de fontes.
- `/admin/sources`: atalho para a área de bases oficiais.
- `/docs`: OpenAPI.

Se o login retornar `Origem da solicitação não autorizada`, confira se o Nginx repassa `X-Forwarded-Host`/`X-Forwarded-Proto` e se você acessa pelo mesmo host/porta que o navegador envia no `Origin`. Não desative validação de origem para resolver o erro.

## Primeiro teste de chave de integração

1. Entre em `/admin/api-keys`.
2. Crie uma chave com escopo `certificates:read`.
3. Copie o segredo exibido uma única vez.
4. Execute:

```bash
export CAEPI_API_KEY='CHAVE_GERADA_NO_ADMIN'
curl -i -H "Authorization: Bearer $CAEPI_API_KEY" \
  -H 'Accept: application/json' \
  http://localhost:8080/api/v1/certificates/15272
```

Use um CA que realmente exista na base local. Espera-se `200` e JSON. `404` significa que o CA ainda não está na base; `401` é chave ausente/inválida/expirada/revogada; `403` indica escopo insuficiente. A consulta externa é read-only e não inicia scraping.

## Executar testes

```bash
docker compose exec -T api pytest -q tests/test_admin_integration_api.py

docker compose exec -T api pytest -q \
  tests/test_batch_processing.py \
  tests/test_api_endpoints.py \
  tests/test_bulk_campaigns.py \
  tests/test_campaign_service.py \
  tests/test_scraper_cloudflare.py \
  tests/test_caepi_scraper.py
```

Para executar todos:

```bash
docker compose exec -T api pytest -q
```

Alguns testes podem depender de variáveis de opt-in ou serviços reais. Leia seus marcadores e instruções no código dos testes antes de executá-los. Não reporte como aprovação de integração real um teste que usou mocks.
