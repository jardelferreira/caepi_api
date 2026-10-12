# Arquitetura do CAEPI API

## Componentes

- **Nginx**: ponto de entrada HTTP local na porta `8080`; encaminha ao serviço `api` na porta interna `8000`.
- **FastAPI (`api`)**: rotas de health, sessão administrativa, integração Bearer, catálogo, importação e acompanhamento operacional.
- **PostgreSQL (`db`)**: certificados consolidados, observações, metadados de documentos e chaves de API (hashes dos segredos).
- **Redis (`redis`)**: broker Celery em DB 1, backend de resultado em DB 2 e histórico operacional em DB 0, conforme as URLs de configuração.
- **Celery worker (`worker`)**: execução de tarefas de scraping/atualização e obtenção de PDFs.
- **Campaign dispatcher (`campaign-dispatcher`)**: envia gradualmente tarefas de campanhas para a fila, sem carregar a fila toda de uma vez.
- **Scheduler (`scheduler`)**: processo Celery Beat; o agendamento automático está vazio (`beat_schedule={}`), e a alimentação de campanhas ocorre no dispatcher.
- **SeaweedFS (`seaweedfs`)**: armazenamento local S3 compatível para PDFs. Porta `8333` fica ligada a `127.0.0.1` no Compose.

## Fluxo de dados

1. O operador inspeciona e importa manualmente uma fonte oficial, depois de validar relatório e simulação.
2. Os certificados são mantidos em PostgreSQL; dados de origem/metadados e observações são preservados no JSON e tabelas existentes.
3. Uma tarefa administrativa explícita enfileira `refresh_source` ou `fetch_certificate_pdf` no Celery.
4. O worker coleta dados se o portal permitir acesso, reconcilia os valores e grava documento/metadata quando apropriado.
5. Consumidores externos consultam somente a base local com chave Bearer e escopo específico; não disparam scraping.

## Modelo de confiança por campo

O cadastro consolidado utiliza o número do CA como identificador. A origem dos valores é guardada em metadados por campo. Valores preenchidos do portal têm precedência sobre a base oficial nos campos correspondentes; um valor vazio não apaga um valor válido existente. Divergências devem ser expostas para análise, não tratadas como prova automática de que uma fonte está incorreta.

## Assíncrono e limites

- Refresh e PDF são tarefas assíncronas e retornam `202 Accepted` com `task_id`.
- Lote manual pequeno: até 10 CAs únicos por requisição.
- Campanhas em massa: selecionam um snapshot explícito de candidatos e suportam no máximo 50.000 CAs elegíveis conforme a configuração da API.
- Dispatcher mantém no máximo quatro tarefas de campanha em voo (`MAX_IN_FLIGHT = 4`). O worker Selenium também tem configuração própria de concorrência.
- Campanhas exigem confirmação explícita; a prévia não enfileira tarefas.

## Páginas web

- `/login`: login administrativo.
- `/admin`: página inicial do painel.
- `/admin/consult`: consulta local individual.
- `/admin/api-keys`: criar, listar e revogar chaves externas.
- `/certificates`: catálogo, alertas de integridade, lotes e campanhas.
- `/dashboard`: monitor de tarefas e módulos operacionais de fontes oficiais.
- `/admin/sources`: atalho para gerenciamento de fontes.

## Fonte oficial versus portal

A base oficial e o scraping são fontes distintas. O importador exige inspeção e simulação antes de gravar. As consultas externas apenas leem a base consolidada. Enquanto o portal estiver devolvendo desafio Cloudflare e o arquivo FTP não for um ZIP íntegro, o sistema deve manter o catálogo local sem alegar que está atualizado em tempo real.
