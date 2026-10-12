# CAEPI API

API e painel administrativo para consultar, reconciliar e enriquecer registros de Certificados de Aprovação (CA) de Equipamentos de Proteção Individual (EPI).

O projeto combina uma base local no PostgreSQL, tarefas assíncronas com Celery/Redis, armazenamento de PDFs em um serviço S3 compatível (SeaweedFS no ambiente local), inspeção/importação manual da base oficial e consultas de integração autenticadas por chave de API.

> **Estado operacional conhecido:** a base de CAs já foi importada e o fluxo de tarefas/lotes foi exercitado. O portal CAEPI retornou HTTP 403 com `Cf-Mitigated: challenge` durante o scraping; não contorne a proteção. O arquivo `tgg_export_caepi.zip` do FTP também foi recebido com o tamanho anunciado, mas preenchido por zeros e não era um ZIP válido. A lista local continua utilizável; a atualização da base deve ser adiada até uma fonte válida estar disponível. Consulte `docs/DIAGNOSTICO.md`.

## Conteúdo

- [Instalação e primeiros testes](docs/INSTALACAO_E_PRIMEIROS_TESTES.md)
- [Arquitetura](docs/ARQUITETURA.md)
- [API e integração com sistemas externos](docs/API_E_INTEGRACAO.md)
- [Painel administrativo e importação de bases](docs/ADMINISTRACAO_E_BASES.md)
- [Campanhas e processamento em lote](docs/CAMPANHAS_E_PROCESSAMENTO.md)
- [Segurança, integridade e backups](docs/SEGURANCA_E_BACKUPS.md)
- [Diagnóstico de problemas](docs/DIAGNOSTICO.md)
- [Índice da documentação](docs/README.md)

## Requisitos

- Docker Engine e Docker Compose v2.
- Acesso ao terminal e permissão para executar `docker compose`.
- Portas locais `8080` (Nginx/API) e `8333` (SeaweedFS, vinculada ao loopback) disponíveis.
- Para testes de desenvolvimento: Python 3.12 está incluído na imagem da API; não é obrigatório instalar Python no host.

## Instalação rápida

### 1. Obter o projeto

Se você já possui o repositório, entre na raiz — a pasta que contém `docker-compose.yml`:

```bash
cd ~/Downloads/caepi-api-selenium
```

Se estiver começando de uma cópia nova, clone ou extraia o projeto e entre nessa pasta antes de continuar. Não copie um `.env` de outra instalação por cima do existente.

### 2. Configurar o ambiente

Confira se `.env` existe. Se já existe e os containers funcionam, preserve-o; acrescente ou atualize apenas as variáveis necessárias. **Nunca envie o `.env` para o GitHub nem o compartilhe em mensagens.**

O login administrativo depende destas variáveis:

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=COLOQUE_UMA_SENHA_FORTE
ADMIN_SESSION_SECRET=COLOQUE_UM_SEGREDO_ALEATORIO_COM_32_BYTES_OU_MAIS
ADMIN_COOKIE_SECURE=false
```

Gere valores aleatórios no Ubuntu:

```bash
openssl rand -hex 16
openssl rand -hex 32
```

Use o primeiro para `ADMIN_PASSWORD` e o segundo para `ADMIN_SESSION_SECRET`. `ADMIN_COOKIE_SECURE=false` é apenas para desenvolvimento local em HTTP (`http://localhost:8080`). Em produção, atrás de HTTPS, use `true`.

A configuração Docker utiliza `POSTGRES_DB`, `POSTGRES_USER` e `POSTGRES_PASSWORD` para o PostgreSQL e `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` e `S3_BUCKET` para o armazenamento local. Confira os valores do seu `.env` antes de alterar qualquer um; trocar senhas de um volume já criado pode impedir a conexão.

O endpoint de upload/importação usa `python-multipart`. Confirme que `pyproject.toml` inclui `python-multipart` em `project.dependencies`. Se estiver ausente, adicione `"python-multipart>=0.0.20,<1.0",` dentro da lista existente de `dependencies` antes de construir a imagem.

### 3. Validar e iniciar os serviços

```bash
docker compose config -q
docker compose up -d --build
docker compose ps
```

A API executa `alembic upgrade head` na inicialização. A migração de chaves de integração é aditiva; ainda assim, mantenha backups antes de atualizações importantes. Não use `docker compose down -v`: isso remove volumes persistentes.

Aguarde `api`, `db` e `redis` ficarem saudáveis. O `campaign-dispatcher` deve permanecer em execução para alimentar campanhas controladas.

### 4. Verificar a API

```bash
curl -i http://localhost:8080/api/v1/health/live
curl -i http://localhost:8080/api/v1/health/ready
```

Esperado: `200 OK`. O primeiro confirma que a aplicação responde; o segundo confirma também a conexão com o PostgreSQL.

Verifique a dependência usada por uploads:

```bash
docker compose exec -T api python -c "import multipart; print('python-multipart OK')"
```

### 5. Entrar no painel

Abra:

- Login: <http://localhost:8080/login>
- Início administrativo: <http://localhost:8080/admin>
- Catálogo e campanhas: <http://localhost:8080/certificates>
- Monitor de tarefas: <http://localhost:8080/dashboard>
- Consulta individual: <http://localhost:8080/admin/consult>
- Chaves de integração: <http://localhost:8080/admin/api-keys>
- Bases oficiais: <http://localhost:8080/admin/sources>
- OpenAPI/Swagger: <http://localhost:8080/docs>

Entre com `ADMIN_USERNAME` e `ADMIN_PASSWORD`. Se o login não estiver configurado, a página indicará quais variáveis faltam. A autenticação administrativa usa sessão assinada, expiração de oito horas e proteção CSRF para operações mutáveis.

## Primeiro teste de consulta externa

### 1. Gere uma chave no admin

Em **Chaves de API**, crie uma chave identificada (por exemplo, `integracao-teste`) com o escopo `certificates:read`. Copie o segredo completo uma única vez e guarde-o num gerenciador de segredos. O banco guarda apenas o hash; o segredo não pode ser mostrado novamente.

### 2. Consulte um CA já existente

No terminal, defina a chave sem gravá-la no código:

```bash
export CAEPI_API_KEY='COLE_A_CHAVE_GERADA_NO_ADMIN'
```

Faça a consulta read-only:

```bash
curl -sS \
  -H "Authorization: Bearer $CAEPI_API_KEY" \
  -H 'Accept: application/json' \
  http://localhost:8080/api/v1/certificates/15272
```

O endpoint consulta o registro local e não inicia scraping. `404` significa que o CA não está cadastrado localmente. Para baixar um PDF existente, crie/atualize uma chave com o escopo `documents:read` e consulte `/api/v1/certificates/15272/pdf`.

Consulte [API e integração](docs/API_E_INTEGRACAO.md) para exemplos em Python, modelo de resposta e códigos HTTP.

## Testes iniciais

Execute primeiro os testes de autenticação e integração, depois os testes operacionais:

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

A suíte completa também pode ser executada:

```bash
docker compose exec -T api pytest -q
```

Alguns testes de integração podem estar marcados como opt-in e ser ignorados por padrão. Consulte as opções definidas nos próprios testes antes de ativá-los. Avisos de depreciação do Starlette não equivalem a falhas, mas devem ser acompanhados em atualizações futuras.

## Rotas essenciais

| Método | Endpoint | Uso |
|---|---|---|
| `GET` | `/api/v1/health/live` | Verificação de vida |
| `GET` | `/api/v1/health/ready` | Verificação de prontidão e banco |
| `GET` | `/api/v1/certificates/{ca_number}` | Consulta local autenticada por chave Bearer (`certificates:read`) |
| `GET` | `/api/v1/certificates/{ca_number}/pdf` | Download de PDF existente (`documents:read`) |
| `POST` | `/api/v1/certificates/{ca_number}/refresh` | Enfileirar atualização por scraping; requer sessão admin |
| `POST` | `/api/v1/certificates/{ca_number}/pdf` | Enfileirar obtenção/atualização do PDF; requer sessão admin |
| `GET` | `/api/v1/certificates/tasks/{task_id}` | Consultar estado de uma tarefa administrativa |
| `GET` | `/api/v1/certificates/catalog` | Consultar/pesquisar o catálogo pela área admin |
| `GET` | `/api/v1/certificates/integrity/summary` | Indicadores de integridade para o painel |
| `GET` | `/api/v1/certificates/campaigns` | Listar campanhas administrativas |
| `GET` | `/api/v1/tasks/summary` | Resumo do histórico de tarefas administrativo |
| `GET` | `/docs` | Documentação OpenAPI interativa |

As rotas administrativas de catálogo, tarefas, campanhas e importações exigem sessão. As rotas externas de consulta usam chave Bearer e escopos; elas não iniciam scraping.

## Comandos operacionais úteis

```bash
# Estado dos containers
docker compose ps

# Logs recentes da API e do worker
docker compose logs --tail=100 api worker

# Logs do dispatcher de campanhas
docker compose logs --tail=100 campaign-dispatcher

# Reiniciar somente Nginx, por exemplo após recriar a API
docker compose restart nginx

# Ver status da API direto pelo Nginx
curl -i http://localhost:8080/api/v1/health/ready
```

## Limitações conhecidas

- O portal CAEPI apresentou HTTP 403 com `Cf-Mitigated: challenge`; não tente contornar esse desafio. As atualizações por scraping devem permanecer paradas enquanto o bloqueio persistir.
- O servidor FTP anunciou `tgg_export_caepi.zip`, mas o download testado continha apenas bytes `0x00` e não era ZIP válido. Não faça nova importação a partir desse arquivo até que a inspeção indique ZIP válido e TXT esperado.
- O CSV `base-de-dados-do-CAEPI-19052025.csv` enviado para diagnóstico também estava sem dados úteis. Não o importe.
- A base local já importada continua utilizável para consultas. Consultar a API não implica que os dados estejam atualizados em tempo real.
- Este repositório não deve ser considerado pronto para exposição pública até concluir os itens de segurança, backup/restore e validação em ambiente HTTPS descritos em `docs/SEGURANCA_E_BACKUPS.md`.

## Licença e uso

Verifique as condições de uso da base oficial do MTE e o contexto da sua implantação. Proteja as chaves, os PDFs e o acesso administrativo de acordo com a política da organização.