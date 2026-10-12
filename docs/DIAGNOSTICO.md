# Diagnóstico e incidentes conhecidos

## 502 Bad Gateway no navegador

O Nginx não consegue uma resposta válida da API. Verifique primeiro:

```bash
docker compose ps
docker compose logs --tail=100 api
docker compose logs --tail=80 nginx
```

Se a API estiver saudável e a única mensagem for `connect() failed (111: Connection refused)` apontando para um IP antigo do serviço, reinicie Nginx:

```bash
docker compose restart nginx
```

Se a API estiver reiniciando, corrija o erro da API antes de reiniciar Nginx. Não remova volumes.

## API encerra por `python-multipart` ausente

FastAPI precisa dessa dependência para endpoints que usam `File`/`UploadFile`. Confirme `python-multipart>=0.0.20,<1.0` em `pyproject.toml` e reconstrua a imagem:

```bash
docker compose up -d --build --force-recreate api
```

## Login retorna “Origem da solicitação não autorizada”

A verificação compara o cabeçalho `Origin` com host/esquema encaminhados. Confirme que `deploy/nginx/default.conf` repassa corretamente `X-Forwarded-Host` e `X-Forwarded-Proto` e que você acessa usando o mesmo host/porta, por exemplo `http://localhost:8080`. Não remova a proteção CSRF/origin.

## Login não configurado

Confirme no `.env` as variáveis:

```dotenv
ADMIN_USERNAME=...
ADMIN_PASSWORD=...
ADMIN_SESSION_SECRET=...
ADMIN_COOKIE_SECURE=false
```

O segredo de sessão precisa ter ao menos 32 caracteres. Recrie apenas a API para carregar novas variáveis e reinicie Nginx se os IPs internos mudarem.

## Portal CAEPI e Cloudflare

Diagnóstico confirmado anteriormente:

- HTTPS para `caepi.trabalho.gov.br` devolveu `403` com cabeçalho `Cf-Mitigated: challenge` e HTML “Just a moment...”.
- O navegador de automação ficou em `data:,`, gerando screenshots brancos e `TimeoutException` para o formulário.
- O host HTTP antigo `caepi.mte.gov.br` falhou com `Network is unreachable` no container.

Isso é uma restrição de acesso/um bloqueio externo. Não tente contornar a proteção. Não lance campanhas novas enquanto o desafio persistir. O catálogo local continua disponível e a API de leitura externa não depende de um acesso ao portal durante a consulta.

## TXT via FTP não é um ZIP válido

O FTP anunciou `tgg_export_caepi.zip` com 8.026.536 bytes e data `20261012130006`. A transferência terminou com tamanho correspondente, mas os bytes recebidos eram todos `0x00`, sem assinatura `PK` e sem entrada ZIP válida. O teste ocorreu dentro e fora do Docker. O servidor listou apenas o ZIP; `tgg_export_caepi.txt` diretamente não existia nesse diretório.

Ação: adiar importação. Não extrair nem aceitar arquivo apenas com base em nome, data ou tamanho. Verificar novamente mais tarde e exigir assinatura ZIP válida, teste de integridade e o TXT esperado.

## CSV oficial inválido

O arquivo enviado para diagnóstico `base-de-dados-do-CAEPI-19052025.csv` estava preenchido por campos vazios, sem cabeçalho útil ou números de CA. Não importar esse arquivo nem preencher campos manualmente para fazê-lo passar.

O operador decidiu não usar o XLSX como alternativa. Manter apenas as fontes TXT via FTP e CSV, e esperar que uma dessas fontes esteja válida.

## Campanha tem falhas

Veja a linha do tempo e mensagens do worker:

```bash
docker compose logs --since=2h worker | grep -Ei 'retry|failed|RuntimeError|TimeoutException|challenge|forbidden'
```

Identifique se é erro transitório, bloqueio do portal ou erro de dados. Não repetir todo o lote se apenas alguns CAs falharam. Depois de corrigir/validar a causa, use recuperação somente para falhas definitivas conforme a campanha.

## Histórico Redis

Redis tem AOF e volume persistente no Compose local. Reiniciar o serviço já foi testado e manteve os registros. Isso não prova recuperação após perda do volume; mantenha backups e não execute comandos de remoção de volumes.

## Avisos de testes

Avisos de depreciação do Starlette não significam falha do teste, mas devem ser resolvidos em atualização futura de dependências. A integração real deve ser validada no Docker; testes com mocks não comprovam acesso externo, disponibilidade de FTP ou portal.
