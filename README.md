# munny

Agregador pessoal de gastos: liga-se a contas bancárias via **Enable Banking**
(PSD2), guarda o histórico de transações em SQLite, categoriza-as (regras +
LLM) e expõe tudo por um **dashboard web** e um **servidor MCP**.

> **Aviso.** Projeto pessoal, partilhado como referência de engenharia. Lida com
> dados bancários reais — usa por tua conta e risco. Não é aconselhamento
> financeiro. A integração Enable Banking exige uma aplicação registada
> (ambiente *Restricted Production* ou *Sandbox*).

## Como funciona

```
Enable Banking (PSD2)  ──►  app/sync.py  ──►  SQLite  ──►  dashboard  (/dashboard)
      consentimento          scheduler 6h       .db file        └─►  MCP  (/mcp)
```

- **Fonte de dados** (`app/sources/enable_banking.py`) — cliente Enable Banking:
  JWT RS256, fluxo de consentimento PSD2 redirect-based, saldos e transações com
  paginação por `continuation_key`. Uma fonte cobre vários bancos (ASPSPs).
- **Sincronização** (`app/sync.py`) — corre a cada 6h (APScheduler embutido).
  Watermark por conta (`last_synced_at`), inserção idempotente
  (`UNIQUE(source, external_id)`), guarda-costas do limite PSD2 de 4 pedidos
  não-assistidos/dia por ASPSP.
- **Categorização** (`app/categorize.py` + `categories.yaml`) — primeiro regras
  por keyword; o resto vai num único pedido em batch a um LLM (Gemini), que
  recebe a definição de cada categoria e o nome do comerciante e devolve também
  uma justificação. Edições manuais no dashboard sobrevivem às sincronizações.
- **Dashboard** (`app/dashboard.py` + `app/templates/`) — páginas
  server-rendered (Jinja2 + HTMX + Chart.js via CDN, sem build): saldos, extrato
  filtrável e gráfico de gastos por categoria. Login com password única e cookie
  de sessão assinado (`app/auth.py`).
- **Servidor MCP** (`app/mcp_server.py`) — Streamable HTTP em `/mcp`, bearer
  auth. Tools: `get_balance`, `list_transactions`, `spending_by_category`,
  `compare_sources`, `connection_status`.

## Stack

Python 3.12 · FastAPI · SQLite (stdlib `sqlite3`) · APScheduler · MCP SDK ·
`google-genai` · Docker.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
pytest
```

## Configuração

Preencher o `.env`:

| Variável | Para quê |
|---|---|
| `ENABLE_BANKING_APPLICATION_ID` | ID da aplicação registada em [enablebanking.com](https://enablebanking.com/) |
| `ENABLE_BANKING_PRIVATE_KEY_B64` | Chave privada RSA da aplicação, em base64 (`base64 -w0 private.pem`) |
| `ENABLE_BANKING_REDIRECT_URL` | URL de callback registado (default: `http://localhost:8000/connect/enable-banking/callback`) |
| `MCP_BEARER_TOKEN` | Segredo — serve de bearer token do MCP **e** de password do dashboard |
| `GEMINI_API_KEY` | Chave da API Gemini para o fallback de categorização |
| `DB_PATH` | Caminho do ficheiro SQLite (default: `./data/trocado.db`) |

Os bancos a ligar definem-se em `ENABLE_BANKING_ASPSPS` (`app/config.py`) — nome
e país do ASPSP conforme o catálogo do Enable Banking.

## Correr

```bash
uvicorn app.main:app --reload
```

- `GET /connect/enable-banking/start/{aspsp_key}` — inicia o consentimento de um banco
- `GET /dashboard` — dashboard (login com o `MCP_BEARER_TOKEN`)
- `GET /status` — estado dos consentimentos PSD2
- `POST /mcp` — servidor MCP (header `Authorization: Bearer <MCP_BEARER_TOKEN>`)

## Deploy

- **Railway** — `Dockerfile` corre `uvicorn` na porta `$PORT`; definir as
  variáveis de ambiente no painel; volume persistente montado em `DB_PATH`.
  O scheduler embutido só funciona com **1 réplica**.
- **VM / self-host** — `docker-compose.yml` + `Caddyfile` (reverse proxy com TLS
  automático); editar o domínio no `Caddyfile`.

## Estrutura

```
app/
  main.py            FastAPI app, middleware de auth, lifespan (scheduler + MCP)
  config.py          variáveis de ambiente e constantes
  db.py              schema SQLite e helpers de query (sem ORM)
  sources/
    enable_banking.py  cliente Enable Banking
  sync.py            orquestrador de sincronização
  categorize.py      regras + fallback LLM
  categories.yaml    keywords das regras
  mcp_server.py      tools MCP
  dashboard.py       rotas do dashboard
  auth.py            segredo partilhado + cookie de sessão
  templates/         páginas Jinja2
tests/               pytest, sem fixtures externas
```

## Licença

MIT — ver [LICENSE](LICENSE).
