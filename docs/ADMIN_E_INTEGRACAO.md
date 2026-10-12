# Administração e integração da CAEPI API

## 1. Configuração inicial do administrador

A área administrativa fica desabilitada até que as três variáveis abaixo existam. Gere valores fortes no Ubuntu:

```bash
openssl rand -hex 16   # senha aleatória para o ADMIN_PASSWORD
openssl rand -hex 32   # segredo para o ADMIN_SESSION_SECRET
```

Adicione ao `.env` já existente, sem substituir outras variáveis:

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=COLE_A_SENHA_GERADA
ADMIN_SESSION_SECRET=COLE_O_SEGREDO_GERADO
ADMIN_COOKIE_SECURE=false
```

`ADMIN_COOKIE_SECURE=false` é apropriado somente para desenvolvimento local em `http://localhost:8080`. Em produção, atrás de HTTPS, defina `ADMIN_COOKIE_SECURE=true`. Não comite `.env`, não compartilhe os valores e não use senhas de exemplo.

Depois de salvar o `.env`:

```bash
docker compose config -q
docker compose up -d --build --force-recreate api worker scheduler campaign-dispatcher
docker compose restart nginx
docker compose ps
```

No primeiro arranque, o serviço `api` executa `alembic upgrade head` e cria a tabela aditiva `api_keys`. As tabelas `certificates`, `certificate_documents` e `certificate_observations` não são recriadas nem apagadas. Faça backup normal do banco antes de aplicar atualizações de esquema em ambientes importantes.

## 2. Login e módulos de administração

- `/login`: login de administrador.
- `/admin`: página inicial e navegação por módulos.
- `/dashboard`: monitor de tarefas.
- `/certificates`: catálogo, integridade e campanhas.
- `/admin/consult`: consulta individual pela base local, sem iniciar scraping.
- `/admin/api-keys`: gerar, listar e revogar chaves de integração.
- `/admin/sources`: inspecionar e importar bases oficiais.
- `/docs`: documentação OpenAPI.

Sessão administrativa usa cookie assinado `HttpOnly`, `SameSite=Strict`, expiração de 8 horas e token CSRF para operações mutáveis. Login tem limitação de tentativas baseada no Redis. O logout revoga a sessão do navegador.

## 3. Gerar uma chave para um sistema externo

Entre em `/admin/api-keys`, dê um nome à integração, selecione os escopos e o prazo. A chave em claro é apresentada somente uma vez. Copie-a para o gestor de segredos do sistema consumidor; nunca a coloque no código-fonte, repositório, front-end público ou logs.

Escopos disponíveis:

- `certificates:read`: consulta o registro consolidado de um CA.
- `documents:read`: baixa o PDF já armazenado no Object Storage.

Os hashes SHA-256 são armazenados no PostgreSQL. É possível revogar uma chave sem alterar a senha administrativa ou outras integrações.

## 4. Consultar um CA por HTTP

Endpoint read-only:

```http
GET /api/v1/certificates/{ca_number}
Authorization: Bearer caepi_live_SUA_CHAVE
Accept: application/json
```

Exemplo local:

```bash
export CAEPI_API_KEY='cole-a-chave-aqui'
curl -sS \
  -H "Authorization: Bearer $CAEPI_API_KEY" \
  -H 'Accept: application/json' \
  http://localhost:8080/api/v1/certificates/15272
```

A consulta lê apenas os dados já existentes no PostgreSQL. Não chama o portal, não inicia uma tarefa Celery e não atualiza o registro. Se o CA não estiver cadastrado, a resposta é `404`; a integração deve decidir se solicita atualização em uma interface administrativa ou se aguarda uma atualização autorizada.

Resposta simplificada:

```json
{
  "certificate": {
    "ca_number": "15272",
    "status": "VÁLIDO",
    "valid_until": "2030-01-31",
    "manufacturer": "Fabricante exemplo",
    "equipment_name": "Equipamento de proteção",
    "brand": "Marca exemplo",
    "last_source": "caepi_portal",
    "last_source_update": "2026-10-12T10:30:00+00:00",
    "data": {},
    "sources": {},
    "documents": {
      "certificate_pdf_available": true,
      "certificate_pdf_sha256": "...",
      "certificate_pdf_size_bytes": 156572
    }
  },
  "source": "local_database",
  "scraping_started": false
}
```

Os valores acima são apenas um exemplo de formato, não afirmações sobre o CA 15272 atual. Campos podem ser `null`, e os objetos `data`/`sources` refletem aquilo que foi efetivamente coletado.

## 5. Exemplo em Python

```python
import os
import requests

base_url = os.getenv("CAEPI_API_URL", "http://localhost:8080")
api_key = os.environ["CAEPI_API_KEY"]
ca_number = "15272"

response = requests.get(
    f"{base_url}/api/v1/certificates/{ca_number}",
    headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    timeout=15,
)
if response.status_code == 404:
    print("CA ainda não cadastrado na base local")
elif response.status_code == 401:
    raise RuntimeError("Chave ausente, inválida, expirada ou revogada")
else:
    response.raise_for_status()
    certificate = response.json()["certificate"]
    print(certificate["ca_number"], certificate["status"], certificate["valid_until"])
```

## 6. Consultar o PDF armazenado

Requer uma chave com escopo `documents:read`:

```bash
curl -fS -L \
  -H "Authorization: Bearer $CAEPI_API_KEY" \
  http://localhost:8080/api/v1/certificates/15272/pdf \
  -o CA-15272.pdf
```

O endpoint apenas entrega um PDF existente no armazenamento. Se o documento não estiver disponível, retorna `404`; não inicia download nem scraping.

## 7. Códigos de resposta

- `200`: consulta ou download realizado.
- `201`: chave de API criada (o segredo completo é devolvido nesta resposta uma única vez).
- `401`: falta chave/sessão ou a chave expirou, foi revogada ou é inválida.
- `403`: escopo insuficiente, token CSRF inválido ou origem recusada.
- `404`: CA/documento/chave não encontrado.
- `422`: número de CA ou parâmetros inválidos.
- `429`: limite de tentativas de login excedido.
- `503`: configuração administrativa ausente ou dependência necessária indisponível.
- `502`: falha no acesso ao armazenamento de PDF.

## 8. Proteções e limitações

- A chave externa tem somente os escopos escolhidos e não pode iniciar scraping, campanhas ou importações.
- As rotas de operações administrativas exigem sessão e token CSRF.
- A consulta externa é read-only; nenhuma consulta por API altera dados.
- O PDF pode conter dados oficiais protegidos; entregue `documents:read` apenas a sistemas que realmente necessitem dele.
- Não exponha Redis/PostgreSQL/SeaweedFS à Internet. Em produção, use HTTPS, segredos fortes e rotacionáveis e backups fora do host.
- Não contorne desafios do Cloudflare do portal CAEPI. O endpoint de consulta local continua útil com os dados já carregados enquanto o portal não estiver acessível.
