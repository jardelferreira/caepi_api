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
