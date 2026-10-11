# CAEPI API

API para consulta e enriquecimento de Certificados de Aprovação (CA) de EPIs.

## Stack
- Python 3.12, FastAPI e SQLAlchemy 2
- PostgreSQL para dados consolidados e observações históricas
- Redis + Celery para executar consultas em segundo plano
- Selenium + Chromium/ChromeDriver para consultar o portal CAEPI
- SeaweedFS local e Cloudflare R2 em produção, via API compatível com S3
- Nginx como proxy reverso

## Executar
```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
```

API: http://localhost:8080  
Swagger: http://localhost:8080/docs  
Liveness: http://localhost:8080/api/v1/health/live  
Readiness: http://localhost:8080/api/v1/health/ready

## Consultar um CA
A API enfileira o trabalho no Celery. Exemplo:
```bash
curl -X POST http://localhost:8080/api/v1/certificates/42049/refresh
curl -X POST http://localhost:8080/api/v1/certificates/42049/pdf
```
Cada resposta inclui `task_id`. Consulte o estado com:
```bash
curl http://localhost:8080/api/v1/certificates/tasks/ID_DA_TAREFA
```

O worker executa o Chromium headless via Selenium. Os PDFs são validados pela assinatura `%PDF-`, armazenados no SeaweedFS/R2 e registrados em `certificate_documents`. Os dados consolidados são mesclados por campo: valores vazios de uma consulta não apagam valores já conhecidos. Alterações coletadas são registradas em `certificate_observations`.

## Logs e operação
```bash
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f scheduler
docker compose exec api pytest
docker compose down
```
`docker compose down` preserva volumes. `docker compose down -v` também remove os volumes de dados.

## Configuração opcional do Selenium
- `CAEPI_URL`: substitui a URL principal do portal.
- `IGNORE_HTTPS_ERRORS=1`: ignora erros de certificado TLS, somente para diagnóstico.
- `CHROMIUM_BIN`: caminho do Chromium (padrão no container: `/usr/bin/chromium`).
- `CHROMEDRIVER_BIN`: caminho do ChromeDriver (padrão no container: `/usr/bin/chromedriver`).

Não contorne autenticação, CAPTCHA ou outras proteções do portal; respeite os termos e limites de acesso. Se o portal mudar de estrutura ou bloquear a sessão automatizada, consulte os logs do worker para identificar a etapa que falhou.


## Testes automatizados

Instale as dependências de desenvolvimento no ambiente Python:

```bash
pip install -e ".[dev,scraping]"
pytest -q
```

Os testes de idempotência usam banco SQLite temporário e armazenamento simulado. Eles não acessam o portal CAEPI nem alteram os dados do Docker. Cobrem a coleta repetida, o primeiro download, a deduplicação por SHA-256, a deduplicação por texto extraído e a criação de uma nova versão quando o conteúdo textual muda.

Para executar os testes dentro do contêiner da API, com as dependências de teste instaladas na imagem:

```bash
docker compose exec -T api pytest -q
```


## Testes de integração reais (PostgreSQL + SeaweedFS)

A suíte padrão usa dependências simuladas e não altera dados do ambiente. Para validar também o PostgreSQL e o SeaweedFS reais do Docker Compose, execute explicitamente o teste de integração abaixo. Ele substitui apenas o portal externo CAEPI por um scraper sintético e cria um CA de teste com número exclusivo. Ao final, remove somente o certificado de teste, seus registros relacionados e os objetos que criou.

```bash
docker compose exec -T -e CAEPI_RUN_INTEGRATION=1 api pytest -q -m integration tests/test_integration_postgres_seaweedfs.py
```

O teste valida a persistência no PostgreSQL, a leitura do PDF do SeaweedFS, a correspondência do SHA-256 e a deduplicação por hash na segunda execução. Não use `docker compose down -v` para executar os testes.