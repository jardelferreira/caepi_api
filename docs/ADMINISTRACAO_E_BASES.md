# Administração e gestão das bases oficiais

## Login administrativo

O login só é habilitado quando `ADMIN_USERNAME`, `ADMIN_PASSWORD` e `ADMIN_SESSION_SECRET` estão configurados; o segredo de sessão deve ter pelo menos 32 caracteres. A sessão expira após oito horas. Login usa comparação segura de credenciais e limite de tentativas via Redis. Métodos mutáveis exigem CSRF e a origem é verificada.

## Módulos da dashboard

| Página | Função |
|---|---|
| `/admin` | Entrada e navegação entre módulos |
| `/admin/consult` | Consultar um CA cadastrado na base local |
| `/admin/api-keys` | Criar, listar e revogar chaves de integração |
| `/certificates` | Pesquisa, filtros, integridade, seleção de CAs e campanhas |
| `/dashboard` | Monitor de tarefas e gerenciamento de fontes oficiais |
| `/admin/sources` | Atalho para gerenciamento de bases |
| `/docs` | OpenAPI interativo |

## Importação: processo seguro

O processo esperado é **consultar fonte → inspecionar → comparar → simular → confirmar gravação**. Não há importação automática por carregar a página. A simulação valida o arquivo e estima o efeito, sem persistir os certificados.

A inspeção inclui, conforme a fonte disponível, nome do arquivo, metadados de data, tamanho, hash SHA-256, total de linhas, CAs únicos e linhas inválidas. O operador deve comparar os quantitativos antes de confirmar.

### Formatos suportados pelo importador

- TXT delimitado por `|` com os dados de referência de 19 colunas.
- CSV/TXT local enviado pelo operador, com cabeçalho reconhecível e formato/delimitador compatíveis.
- CSV anunciado na página do MTE quando o link e o conteúdo estiverem disponíveis e válidos.

O importador deve rejeitar arquivo vazio, resposta HTML, arquivo sem cabeçalho reconhecível, ZIP inválido ou conteúdo inconsistente. O nome/extensão sozinho não prova integridade.

### Situação conhecida das fontes

- O arquivo FTP `tgg_export_caepi.zip` foi anunciado com aproximadamente 8 MB e transferido com o tamanho esperado, mas o conteúdo recebido foi composto por bytes zero (`0x00`); não era ZIP válido nos testes dentro e fora do Docker.
- O CSV `base-de-dados-do-CAEPI-19052025.csv` enviado para diagnóstico continha linhas sem dados úteis.
- Não usar o XLSX como substituto, conforme decisão do operador do projeto.
- A atualização pela fonte remota fica adiada até que o TXT/ZIP ou CSV esperado possa ser validado. A base previamente importada continua em uso.

### Regras de reconciliação

- Número do CA normalizado é a chave de correspondência.
- Ocorrências históricas repetidas dentro do TXT não devem ser confundidas com registros inválidos; o cadastro consolidado pode usar a ocorrência mais recente de acordo com a lógica do importador.
- A origem dos campos deve permanecer rastreável.
- Campo vazio não deve substituir um valor existente.
- Os valores extraídos do portal têm precedência onde o portal possui valor confiável, mas o portal pode estar indisponível; divergência é um alerta, não exclusão automática de dados.
- CAs que desaparecerem de uma versão nova da fonte devem ser sinalizados, nunca apagados automaticamente.

## Cadastro e consulta

A consulta individual administrativa lê o PostgreSQL sem provocar scraping. Para enriquecer dados, o operador precisa enfileirar explicitamente `refresh` ou criar uma campanha. A consulta por API externa também é read-only.

## Cuidados de operação

1. Confirme a fonte e o hash antes de simular.
2. Confira total de novos, atualizados, inalterados, rejeitados e divergências.
3. Não confirme se o relatório está vazio ou incoerente com o esperado.
4. Mantenha cópia/backup antes de grandes importações.
5. Não execute `docker compose down -v` para resolver falhas da interface.
