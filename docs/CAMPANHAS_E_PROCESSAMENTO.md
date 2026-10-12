# Campanhas e processamento assíncrono

## Duas formas de enfileirar

### Lote pequeno

Na tela de catálogo, selecione até 10 CAs e escolha atualizar dados ou obter/atualizar PDF. O limite também é aplicado pela API. A resposta inclui `batch_id` e um item por CA com `task_id` e resultado do enfileiramento.

### Campanha controlada

A campanha calcula uma prévia, constrói um snapshot de candidatos e começa somente com confirmação explícita. Escopos atuais:

- `unqueried`: sem consulta recente confirmada ao portal.
- `status_validity_conflict`: situação declarada válida com data de validade expirada.
- `official_portal_divergence`: divergência detectada entre base oficial e portal.
- `any_issue`: pelo menos um alerta de integridade.
- `all`: todos os CAs cadastrados.

A quantidade máxima configurada é 50.000 CAs; não significa que se deva iniciar uma campanha desse tamanho. Comece com 100 e suba o volume apenas após avaliar estabilidade e limites de origem.

O serviço `campaign-dispatcher` alimenta a fila gradualmente, com no máximo quatro tarefas da campanha em voo. O worker Selenium tem configuração de concorrência própria. Não confunda `4 em voo` com quantidade total instantânea de registros pendentes.

## Operações de campanha

- Prévia: `GET /api/v1/certificates/campaigns/preview`.
- Criar: `POST /api/v1/certificates/campaigns` com parâmetros e confirmação.
- Listar: `GET /api/v1/certificates/campaigns`.
- Progresso: `GET /api/v1/certificates/campaigns/{campaign_id}`.
- CSV completo: `GET /api/v1/certificates/campaigns/{campaign_id}/report.csv`.
- Pausar/retomar/cancelar: endpoints `pause`, `resume`, `cancel` sob a campanha.
- Recuperar falhas: `POST /api/v1/certificates/campaigns/{campaign_id}/retry-failed`.

As rotas exigem sessão de administrador e CSRF para ações mutáveis. Consulte `/docs` para o schema atual de cada payload.

## Recomendação de rollout

1. Escolha 2–10 CAs representativos, confirme status e PDF quando o portal estiver acessível.
2. Faça campanha de 100 registros com prévia e confirmação.
3. Observe throughput, tentativas, status final, logs e alertas de integridade.
4. Investigue cada erro definitivo antes de criar campanha de recuperação.
5. Use retry-failed em vez de repetir CAs com sucesso.
6. Só depois de confirmar estabilidade, aumente para 250 ou 500.

## Estados

Os estados mais comuns do Celery são `PENDING`, `STARTED`, `RETRY`, `SUCCESS` e `FAILURE`. Uma campanha também tem estado operacional próprio (`RUNNING`, `PAUSED`, `CANCELLED` ou concluído conforme implementação). Uma tarefa `SUCCESS` confirma que a função terminou sem erro reportado, mas não substitui validação de negócio: confira retorno, dados armazenados e integridade.

## Falhas temporárias e bloqueios

Falhas transientes podem ser repetidas automaticamente conforme a configuração da tarefa. Um desafio do Cloudflare (`HTTP 403`, `Cf-Mitigated: challenge`) deve ser tratado como bloqueio externo, não como seletor errado. Não aumente tentativas para contornar o desafio. Pare a campanha afetada e retome apenas quando houver acesso autorizado/funcional.

## Logs úteis

```bash
docker compose logs --tail=200 worker
docker compose logs --tail=100 campaign-dispatcher
docker compose logs --tail=100 api
```

Não cole logs com credenciais ou cabeçalhos Authorization em tickets públicos.
