# Administração e integração da CAEPI API

Este documento é o ponto de entrada para as instruções operacionais mostradas também na tela de login. A documentação completa está separada por assunto:

- [Instalação e primeiros testes](INSTALACAO_E_PRIMEIROS_TESTES.md)
- [API e integração com sistemas externos](API_E_INTEGRACAO.md)
- [Administração e bases oficiais](ADMINISTRACAO_E_BASES.md)
- [Campanhas e processamento](CAMPANHAS_E_PROCESSAMENTO.md)
- [Segurança e backups](SEGURANCA_E_BACKUPS.md)
- [Diagnóstico](DIAGNOSTICO.md)

## Configurar o login administrativo

Gere valores localmente no Ubuntu:

```bash
openssl rand -hex 16
openssl rand -hex 32
```

Acrescente ao `.env` existente, sem sobrescrever o restante da configuração:

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=COLE_A_SENHA_GERADA
ADMIN_SESSION_SECRET=COLE_O_SEGREDO_GERADO
ADMIN_COOKIE_SECURE=false
```

O segredo de sessão precisa ter no mínimo 32 caracteres. `ADMIN_COOKIE_SECURE=false` é apenas para desenvolvimento em `http://localhost:8080`; em produção HTTPS, use `true`. Nunca publique o `.env` nem compartilhe credenciais.

Depois de salvar:

```bash
docker compose config -q
docker compose up -d --build --force-recreate api worker scheduler campaign-dispatcher
docker compose restart nginx
docker compose ps
```

Não inclua um serviço no comando se ele não existir no seu `docker-compose.yml`. Não execute `docker compose down -v` para corrigir problemas comuns, pois isso remove volumes persistentes.

## Criar uma chave de integração

1. Entre em `http://localhost:8080/login`.
2. Abra `/admin/api-keys`.
3. Crie uma chave nomeada e escolha os escopos necessários.
4. Copie o segredo integral imediatamente para o gerenciador de segredos do sistema consumidor.

- `certificates:read`: consulta de certificado existente na base local.
- `documents:read`: acesso ao PDF já armazenado.

O segredo completo só aparece uma vez; a base guarda um hash SHA-256. Chaves podem expirar ou ser revogadas independentemente da senha do administrador.

## Consultar um CA

```bash
export CAEPI_API_KEY='CHAVE_COPIADA_DO_ADMIN'
curl -sS \
  -H "Authorization: Bearer $CAEPI_API_KEY" \
  -H 'Accept: application/json' \
  http://localhost:8080/api/v1/certificates/15272
```

A consulta usa a base PostgreSQL local e não inicia scraping. Leia [API e integração](API_E_INTEGRACAO.md) para estrutura de resposta, exemplos Python e códigos HTTP.
