# Como o munny funciona por dentro

Este documento explica **Open Banking do zero** e depois percorre o código a
seguir uma única transação — uma compra de café de 2,40 € — desde o momento em
que passas o cartão até apareceres no gráfico de gastos.

Não precisas de saber Python avançado. Precisas de curiosidade.

Índice:

1. [O problema](#1-o-problema)
2. [Open Banking em 10 minutos](#2-open-banking-em-10-minutos)
3. [Ligar uma conta](#3-ligar-uma-conta)
4. [A sincronização](#4-a-sincronização)
5. [Categorizar as transações](#5-categorizar-as-transações)
6. [Mostrar os dados](#6-mostrar-os-dados)
7. [Segurança: o que é segredo e o que não é](#7-segurança-o-que-é-segredo-e-o-que-não-é)
8. [Decisões de engenharia e os seus limites](#8-decisões-de-engenharia-e-os-seus-limites)

---

## 1. O problema

Queres ver os gastos das tuas várias contas (banco tradicional, Wise, etc.)
juntos, categorizados, com gráficos. Os bancos têm apps, mas cada uma só mostra
a sua conta e nenhuma te deixa exportar em condições.

A solução óbvia — "o programa faz login no site do banco por mim e copia os
dados" (*screen scraping*) — é frágil e, hoje, contra os termos de uso da
maioria dos bancos. Há uma via legal e estável: **Open Banking**.

## 2. Open Banking em 10 minutos

### A lei

Em 2015 a União Europeia aprovou a **PSD2** (*Payment Services Directive 2*),
em vigor desde 2018. Uma das suas regras: se o titular de uma conta autorizar,
o banco **tem de** dar a uma aplicação terceira acesso, por API, aos dados dessa
conta (saldos e movimentos) e até à possibilidade de iniciar pagamentos.

Isto criou o "Open Banking": um ecossistema de APIs bancárias com regras comuns.

### Os intervenientes

| Sigla | Quem é | No munny |
|---|---|---|
| **ASPSP** | *Account Servicing Payment Service Provider* — o banco onde tens a conta | O teu banco, a Wise… |
| **TPP** | *Third-Party Provider* — a aplicação que quer aceder aos dados, devidamente licenciada | O Enable Banking (e, por trás dele, o munny) |
| **PSU** | *Payment Service User* — tu | Tu |

Para seres TPP precisas de licença de uma autoridade financeira e de certificados
especiais. Poucos o fazem. A maioria dos programadores usa um **agregador** —
uma empresa que já é TPP licenciada e revende o acesso a centenas de bancos
através de **uma única API**. O munny usa um desses agregadores: o **Enable
Banking**.

Fluxo real:

```
munny  ─►  Enable Banking (é o TPP licenciado)  ─►  cada banco (ASPSP)
```

### O consentimento

Não basta o munny "ter acesso" ao teu banco. Para **cada** ligação, tu tens de:

1. ser **redirecionado para o site/app do próprio banco**;
2. fazer **SCA** (*Strong Customer Authentication*) — autenticação forte: em
   regra, confirmar na app do banco, por SMS, ou com biometria;
3. aprovar explicitamente o âmbito ("esta app pode ler os meus movimentos").

O resultado é um **consentimento** com:

- **prazo** — na PSD2, no máximo 90 dias; depois tens de repetir tudo;
- **âmbito** — só o que aprovaste (aqui: ler contas e transações, não pagar);
- **limite de uso não-assistido** — quando *tu não estás presente* (ex.: uma
  sincronização automática de madrugada), o banco só aceita **cerca de 4
  pedidos por dia** por consentimento. Com o utilizador presente, mais.

Estas três limitações moldam quase todas as decisões do código a seguir.

## 3. Ligar uma conta

Ficheiros: `app/main.py`, `app/sources/enable_banking.py`, `app/config.py`.

### Passo 1 — o utilizador carrega em "ligar banco"

Vais a `GET /connect/enable-banking/start/wise`. O handler
(`enable_banking_start` em `app/main.py`):

```python
auth_url, _ = enable_banking.start_auth(
    aspsp["name"], aspsp["country"], ENABLE_BANKING_REDIRECT_URL,
    state=f"{aspsp_key}:{uuid4()}"
)
return RedirectResponse(auth_url)
```

- `start_auth` (`app/sources/enable_banking.py`) faz `POST /auth` ao Enable
  Banking, a dizer *que* banco, *até quando* quer o consentimento
  (`valid_until` = agora + 90 dias) e para onde devolver o utilizador no fim
  (`redirect_url`). A resposta traz um `url`.
- O **`state`** é uma etiqueta que inventamos (`wise:<uuid aleatório>`). O banco
  vai devolvê-la intacta no fim. Serve para duas coisas: sabermos **que banco**
  era quando voltarmos, e travar pedidos forjados (o `uuid` aleatório funciona
  como *anti-CSRF*).
- Autenticação com o Enable Banking: um **JWT assinado com RSA** (`build_jwt`),
  usando a tua chave privada. Não há "password"; provas a identidade
  assinando cada pedido.

O utilizador é redirecionado para o banco, faz SCA, aprova.

### Passo 2 — o banco devolve o utilizador

O banco redireciona para `GET /connect/enable-banking/callback?code=...&state=wise:...`.
O handler `enable_banking_callback`:

```python
session = enable_banking.exchange_code_for_session(code)   # troca o code por acesso real
valid_until = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
db.upsert_bank_connection(conn, source="enable_banking", aspsp_key=aspsp_key,
                          session_id=session["session_id"], valid_until=valid_until)
for account in session.get("accounts", []):
    details = enable_banking.get_account_details(account["uid"])
    iban = details.get("account_id", {}).get("iban") or account["uid"]
    db.upsert_account(conn, source="enable_banking", aspsp_key=aspsp_key,
                      stable_key=iban, provider_ref=account["uid"], ...)
```

Ponto subtil e importante: **o `uid` de uma conta muda quando reautorizas**, mas
o **IBAN não**. Por isso a chave estável da conta na base de dados
(`accounts.stable_key`) é o **IBAN**, e o `uid` volátil fica só em
`provider_ref`. Assim, daqui a 90 dias, quando renovares o consentimento, o
histórico de transações continua ligado à mesma conta em vez de aparecer uma
conta "nova" e vazia. (Ver o teste `test_reauth_does_not_duplicate_account`.)

O que fica guardado: uma linha em `bank_connections` (a sessão e a validade) e
uma linha por conta em `accounts`. **Nenhuma transação ainda** — isso é da
sincronização.

## 4. A sincronização

Ficheiros: `app/sync.py`, `app/db.py`, `app/main.py`.

### Quando corre

No arranque da app (`app/main.py`), um agendador em memória (APScheduler) é
configurado:

```python
scheduler.add_job(_run_sync_job, "interval", hours=6, next_run_time=datetime.now())
```

`next_run_time=datetime.now()` faz correr **uma vez já**, e depois de 6 em 6
horas. 6h × 4 = 4 corridas por dia — exatamente o limite não-assistido da PSD2.

### O guarda-costas do limite

```python
if not db.check_and_increment_unattended_quota(conn, "enable_banking", aspsp_key, 4):
    logger.warning("limite de 4 pedidos/dia atingido, a saltar")
    continue
```

Um contador por banco por dia, guardado como texto `"2026-08-28:3"`. Se já
estás nos 4, salta esse banco nesta corrida (a próxima tenta outra vez). É um
cinto de segurança: se um *bug* pusesse a app a sincronizar em ciclo, sem isto
o banco bloqueava o consentimento inteiro. (Testes:
`test_unattended_quota_blocks_after_limit`, `test_quota_is_independent_per_aspsp`.)

### Só pedir o que falta — o *watermark*

```python
since = account["last_synced_at"]
date_from = (parse(since) - timedelta(days=2)) if since else (hoje - 90 dias)
date_to = hoje
```

`accounts.last_synced_at` é a **marca de água**: "já tenho tudo até aqui". Na
próxima vez só pedimos de aí para a frente — menos dados, mais rápido, menos
pedidos. Os **2 dias de margem** para trás existem porque uma transação pode ser
lançada pelo banco com data retroativa; sem a margem, perdíamo-la.

### Ir buscar as transações — e a paginação

`get_transactions` (`app/sources/enable_banking.py`) faz `GET
/accounts/{uid}/transactions`. Os bancos devolvem os resultados **por páginas**:
a resposta traz uma `continuation_key` e, para a página seguinte, repetes o
pedido com essa chave.

```python
params = {"date_from": ..., "date_to": ..., "transaction_status": "BOOK"}
while True:
    resp = httpx.get(url, params=params)
    ...
    key = data.get("continuation_key")
    if not key:
        break
    params = {**params, "continuation_key": key}   # <-- repete date_from/date_to!
```

Duas lições aprendidas à força (ver o histórico do repo original):

1. **O pedido de continuação tem de repetir `date_from`, `date_to` e
   `transaction_status`.** Enviar só a `continuation_key` dá erro `422` e a
   última página — a das transações **mais recentes** — nunca chegava.
2. **Se uma página falhar, devolve o que já tens** (`except ...: break`) em vez
   de deitar tudo fora. O *watermark* só avança até onde realmente chegámos, por
   isso a corrida seguinte recupera o resto.

### Normalizar

`to_ledger_transactions` converte o formato do Enable Banking no nosso:

- **sinal no valor**: `credit_debit_indicator == "DBIT"` → valor negativo
  (saída). Assim somar gastos é somar os negativos.
- **descrição**: junta os campos de `remittance_information`.
- **`external_id`**: o identificador único da transação no banco
  (`entry_reference`). **Pode vir vazio.**

### Não duplicar — idempotência

A tabela `transactions` tem `UNIQUE(source, external_id)` e a inserção é
`INSERT OR IGNORE`. Correr a sincronização duas vezes (ou com as janelas de
datas a sobrepor-se, o que acontece sempre por causa da margem de 2 dias) **não
cria duplicados**. (Teste: `test_duplicate_sync_does_not_duplicate_transactions`.)

E quando o banco não dá `external_id`? `app/sync.py` fabrica um, determinístico:

```python
sha256(f"{account_id}|{booking_date}|{amount}|{currency}|{description}")
```

A mesma transação gera sempre o mesmo *hash* → continua a ser idempotente.

### O saldo

Depois das transações, `get_balances` + `pick_balance` obtêm o saldo atual.
Um banco devolve vários "tipos" de saldo (contabilístico, disponível…);
`pick_balance` prefere o **disponível** (`ITAV`), senão o fechado (`CLBD`).

## 5. Categorizar as transações

Ficheiros: `app/categorize.py`, `app/categories.yaml`, `app/sync.py`.

Estratégia **híbrida**, do mais barato para o mais caro:

### Primeiro: regras

`app/categories.yaml` é um dicionário `categoria → [palavras-chave]`:

```yaml
transporte: [uber, bolt, cp, metro, galp, "via verde", ...]
compras:    [continente, "pingo doce", lidl, amazon, worten, ikea, ...]
```

`categorize_rule("PAGAMENTO CONTINENTE MATOSINHOS")` procura cada palavra-chave
na descrição (sem distinguir maiúsculas) e devolve `("compras", "continente")` —
a categoria **e** a palavra que deu o *match*, para podermos mostrar o "porquê"
no painel. Rápido, grátis, previsível.

### Depois: IA, só para o resto

O que as regras não apanham vai **tudo num único pedido** a um modelo de
linguagem (Gemini), em `categorize_llm_fallback`. Um pedido em lote em vez de um
por transação: mais barato e mais rápido.

O *prompt* inclui:

- a **definição escrita de cada categoria** (`CATEGORY_DEFINITIONS`), para o
  modelo não inventar critérios — nomeadamente, `outros` é *só* o resto genuíno
  (transferências entre contas, levantamentos, salário, impostos);
- o **nome do comerciante**, extraído do JSON cru da transação
  (`merchant_text` lê `raw_json.creditor` / `debtor`), não só a descrição;
- o pedido de uma **justificação curta** por transação ("Netflix — serviço de
  streaming"), que também guardamos e mostramos.

A resposta é forçada a um **esquema JSON** com a lista de categorias como *enum*,
por isso o modelo não pode devolver uma categoria inexistente.

### As correções à mão sobrevivem

No painel podes mudar a categoria de uma transação. Isso grava
`category_source = 'manual'`. A rotina de categorização
(`_categorize_pending`) só olha para transações **sem categoria nenhuma**
(`category IS NULL`), por isso uma sincronização futura **nunca** reescreve o
que corrigiste. O botão "Re-categorizar tudo" limpa e refaz só o que **não** é
manual.

## 6. Mostrar os dados

### O painel web

Ficheiros: `app/dashboard.py`, `app/templates/`.

Escolha deliberada: **HTML gerado no servidor** (Jinja2), sem React, sem passo
de *build*, sem `package.json`. Para um painel de uma pessoa, uma SPA seria peso
morto. Só há duas peças de interatividade:

- **filtros** (datas, banco) — um `<form>` normal que recarrega a página.
  Simples e à prova de bugs.
- **editar categoria** — aí sim, um toque de [HTMX](https://htmx.org): o
  `<select>` faz um `POST` e o servidor devolve só a célula atualizada, sem
  recarregar a página.

Os gráficos são [Chart.js](https://www.chartjs.org/) carregado de um CDN. Os
dados do gráfico vão para a página como JSON e o Chart.js desenha o *donut*.

O painel **não fala com o banco** — lê só a base de dados local. Rápido e sem
gastar quota.

### O servidor MCP

Ficheiro: `app/mcp_server.py`.

**MCP** (*Model Context Protocol*) é uma norma para expor dados e ferramentas a
assistentes de IA (como o Claude). O munny expõe 5 ferramentas —
`get_balance`, `list_transactions`, `spending_by_category`, `compare_sources`,
`connection_status` — todas **só de leitura**, todas a partir do SQLite.

Na prática: podes perguntar a um assistente "quanto gastei em restaurantes em
julho?" e ele chama a ferramenta e responde. É montado em `/mcp` e protegido
por *bearer token*.

## 7. Segurança: o que é segredo e o que não é

Ficheiro: `app/auth.py`.

### O que **não** pode aparecer no código nem no Git

- `ENABLE_BANKING_PRIVATE_KEY_B64` — a chave privada. Quem a tiver pode
  fazer-se passar pela tua aplicação.
- `MCP_BEARER_TOKEN` — abre o painel **e** o MCP.
- `GEMINI_API_KEY` — custa dinheiro.

Tudo isto vive **só** em variáveis de ambiente (`.env` localmente, painel do
Railway em produção). O `.gitignore` bloqueia `.env`, a base de dados e a pasta
`secrets/`. **Nenhum destes valores esteve alguma vez no histórico do Git.**

### O que **pode** ser público

Um **IBAN** não é segredo (está em cada fatura que emites) — mas mesmo assim foi
retirado deste repositório, que começou com histórico limpo, para não expor
dados de ninguém.

### O modelo de autenticação do painel

- **Uma password** (o `MCP_BEARER_TOKEN`) para uma app de um utilizador. Sem
  base de dados de utilizadores, sem OAuth — seria complexidade sem retorno.
- **Cookie de sessão assinado**: o valor é `base64(validade)` + `.` +
  `HMAC-SHA256(segredo, base64(validade))`. O servidor consegue **verificar**
  que o cookie foi emitido por ele (a assinatura bate certo) e **quando
  expira**, sem guardar sessões em lado nenhum. Está tudo na biblioteca padrão
  do Python.
- **Falha fechada**: se o `MCP_BEARER_TOKEN` estiver vazio, *nenhum* login passa
  — nem uma string vazia. (Um *bug* clássico é `password == ""` ser aceite
  quando o segredo também é `""`.)
- O *flag* `secure` do cookie só é ativado em HTTPS, para o login continuar a
  funcionar em `http://localhost` durante o desenvolvimento.

### Concorrência

O agendador (que pode estar a meio de uma sincronização) e um clique teu no
painel podem tocar na base de dados ao mesmo tempo. `db.get_conn` ativa o modo
**WAL** do SQLite e um `timeout`, o que chega para este nível de uso.

## 8. Decisões de engenharia e os seus limites

Este projeto segue uma filosofia "preguiçosa": o código mais simples que
resolve, e nada mais. Onde se corta um canto de propósito, há um comentário
`ponytail:` a dizer qual é o limite.

| Decisão | Porquê | Limite / quando trocar |
|---|---|---|
| **SQLite** | Zero configuração, um ficheiro, na biblioteca padrão | Um só processo escreve de cada vez. Milhares de utilizadores → Postgres. |
| **Agendador em memória** | Não precisa de nada externo | Só funciona com **1 réplica**. Escalar → um *cron* externo. |
| **HTML no servidor** | Sem *build*, sem framework de frontend | Interfaces muito ricas → aí talvez compense uma SPA. |
| **Regras + IA em lote** | Barato; a IA só entra no que sobra | A IA custa e erra → daí a correção manual e o *cache* implícito (só categoriza o que está por categorizar). |
| **Uma password partilhada** | Um utilizador, uma app | Multi-utilizador → sistema de contas a sério. |
| **`httpx` síncrono** | Mais fácil de ler e depurar | Muitas contas em paralelo → cliente assíncrono. |

---

### Glossário rápido

- **PSD2** — diretiva europeia que abriu as APIs bancárias.
- **ASPSP** — o banco. **TPP** — a app que acede (via agregador). **PSU** — tu.
- **SCA** — autenticação forte (confirmar que és tu).
- **Consentimento** — autorização com prazo (90 dias) e âmbito.
- **Agregador** — empresa TPP licenciada que revende acesso a muitos bancos (Enable Banking).
- **Watermark** — "já sincronizei até aqui".
- **Idempotência** — repetir a operação não muda o resultado (não duplica).
- **WAL** — modo do SQLite que deixa ler enquanto se escreve.
- **MCP** — protocolo para dar ferramentas/dados a assistentes de IA.
- **JWT** — *token* assinado; aqui, assinado com RSA para provar a identidade da app.
