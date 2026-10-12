# Segurança, integridade e backups

## Segredos

- `.env` é privado e deve estar no `.gitignore`.
- Use senhas fortes para PostgreSQL, admin e SeaweedFS; gere `ADMIN_SESSION_SECRET` com pelo menos 32 bytes aleatórios.
- Não use os valores de exemplo em produção.
- Em HTTP local use `ADMIN_COOKIE_SECURE=false`; em produção via HTTPS use `true`.
- Nunca envie chaves de API, senha administrativa, secret de sessão ou `.env` por chat/tickets.
- Chaves externas são geradas no admin, exibidas uma vez, guardadas como SHA-256 no PostgreSQL, possuem escopos, expiração e revogação.

## Sessão e CSRF

A sessão administrativa tem cookie assinado `HttpOnly` via middleware de sessão, `SameSite=Strict`, expiração de oito horas e opção `Secure` conforme configuração. Operações mutáveis do painel exigem token CSRF. Login é limitado via Redis. Não desative essas proteções para contornar um erro de origem; corrija cabeçalhos Nginx/origem autorizada.

## Rede

- Não exponha PostgreSQL, Redis ou SeaweedFS diretamente à Internet.
- Use Nginx/HTTPS como entrada quando a API ficar disponível fora da máquina local.
- Restrinja acesso do host ao painel administrativo via VPN, firewall ou camada de identidade conforme a implantação.
- Um escopo `documents:read` permite recuperar PDFs; conceda-o apenas a consumidores que precisem disso.
- As rotas externas de integração são read-only; não podem iniciar scraping, campanhas ou importações.

## Verificação de segurança

```bash
git status --short
git check-ignore .env

docker compose config -q
curl -sSI http://localhost:8080/login
```

O segundo comando deve indicar que `.env` está ignorado pelo Git. Não publique `docker compose config` completo em um local público, pois ele pode renderizar valores resolvidos de variáveis.

## Backups mínimos

Faça backups em local distinto do host e teste a restauração. Ajuste nomes/credenciais aos valores reais do `.env`.

### PostgreSQL

```bash
mkdir -p backups
# Exporta schema e dados; o prompt de senha/configuração depende do ambiente.
docker compose exec -T db sh -lc 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "backups/caepi-$(date +%Y%m%d-%H%M%S).sql"
```

O comando usa as variáveis configuradas no container do banco, sem exigir que o shell do host exporte os valores do `.env`. O arquivo SQL deve ser protegido e copiado para outro armazenamento seguro.

### Verificar volumes

```bash
docker volume ls | grep -E 'postgres_data|redis_data|seaweedfs_data'
docker compose exec -T redis redis-cli INFO persistence
```

Redis usa AOF no Compose local. O histórico operacional está em Redis e pode ter política de expiração; não o trate como substituto para auditoria duradoura. Os PDFs ficam no volume do SeaweedFS. Faça snapshot/backup consistente do armazenamento em procedimento próprio e teste recuperar um PDF por chave.

### Segurança contra remoção acidental

Nunca execute `docker compose down -v`, `docker volume prune` ou `docker system prune --volumes` como tentativa genérica de resolver erro 502. Faça backup antes de atualizar migrações ou reconstruir componentes críticos.

## Itens recomendados antes de produção

- HTTPS e `ADMIN_COOKIE_SECURE=true`.
- Política de rotação/revogação para chaves e segredos.
- Backups automatizados do PostgreSQL e object storage com restauração comprovada.
- Métricas/alertas para fila, campanha, erros e armazenamento.
- Rate limit por chave nas rotas de leitura externas se houver exposição pública.
- Retenção e classificação de logs; não incluir tokens ou documentos privados.
- Testes de recuperação do Redis e do storage sem apagar os volumes de produção.
