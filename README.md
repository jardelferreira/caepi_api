# CAEPI API — Importação manual das bases oficiais

O CAEPI API permite comparar e importar manualmente as bases de certificados publicadas pelo Ministério do Trabalho e Emprego (MTE), além de complementar os cadastros com o scraping do portal CAEPI.

## Fontes oferecidas na dashboard

- **TXT via FTP oficial:** `ftp.mtps.gov.br/portal/fiscalizacao/seguranca-e-saude-no-trabalho/caepi/tgg_export_caepi.zip`, contendo o TXT delimitado por `|`. A estrutura segue a abordagem usada no repositório de referência [API_BaseCAEPI](https://github.com/JoaoAugustoMV/API_BaseCAEPI).
- **CSV da página do MTE:** o sistema procura o link CSV dentro da página oficial em vez de fixar permanentemente um caminho de download.
- **Arquivo local:** o operador também pode enviar um `.csv` ou `.txt` já baixado.

A listagem de fontes não baixa nem importa arquivos. A inspeção só ocorre quando o operador clica no botão correspondente. A inspeção calcula a data disponível, o tamanho, o hash, o número de linhas, os CAs únicos e as linhas inválidas. O painel sinaliza a fonte com data identificada mais recente, sem assumir que um campo de modificação de servidor represente necessariamente a data de referência do conteúdo.

## Fluxo de importação

A dashboard aplica o fluxo `inspecionar → comparar → selecionar → simular → confirmar`. O modo de simulação é obrigatório antes de uma gravação. Os arquivos ficam preparados em área temporária por 30 minutos e não são importados automaticamente.

O importador usa o número do CA como chave, aceita ocorrências históricas repetidas no TXT, consolida a última ocorrência no registro principal e registra mudanças usando a tabela de observações existente. A origem de cada campo é preservada em metadados JSON. Os dados do portal CAEPI têm prioridade sobre a base importada nos campos em que o portal fornece valor; campos vazios nunca apagam dados existentes.

## Instalação

Siga `PATCH-INSTALL.md`. Configure `IMPORT_API_KEY` no `.env` existente e reinicie/build apenas `api`, `worker`, `scheduler` e depois o Nginx. Não apague volumes nem substitua o `.env`.

## Endpoints administrativos

- `GET /api/v1/imports/sources`: lista as fontes conhecidas; não baixa nada.
- `POST /api/v1/imports/inspect-source?source_id=mte_ftp_txt`: inspeciona TXT do FTP, sem importar.
- `POST /api/v1/imports/inspect-source?source_id=mte_web_csv`: encontra e inspeciona o CSV do MTE, sem importar.
- `POST /api/v1/imports/inspect-upload`: inspeciona um upload local, sem importar.
- `POST /api/v1/imports/import-staged?staging_token=…&dry_run=true`: simula.
- `POST /api/v1/imports/import-staged?staging_token=…&dry_run=false`: grava após uma simulação recente da mesma fonte e confirmação explícita no painel.

Os endpoints administrativos exigem `X-Import-Key`, preenchido pela dashboard a partir da chave `IMPORT_API_KEY`. Não exponha esse valor no GitHub.