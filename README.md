# munny

**Vê todo o teu dinheiro num sítio só.** O `munny` liga-se às tuas contas
bancárias, descarrega as transações, arruma-as por categoria e mostra-te um
painel com saldos e gráficos de gastos.

É um projeto pessoal, publicado como **material de estudo**: mostra, com código
pequeno e comentado, como se constrói uma integração bancária real (Open
Banking / PSD2), um pipeline de sincronização de dados, categorização com IA e
um painel web — tudo em Python.

> 👉 **Se vieste para aprender**, lê o
> [**guia "Como funciona"**](docs/COMO-FUNCIONA.md): explica Open Banking do
> zero e percorre o código a seguir uma compra de café desde o banco até ao
> gráfico.

> ⚠️ **Aviso.** Lida com dados bancários reais. Usa por tua conta e risco, não é
> aconselhamento financeiro, e ligar-te a bancos exige registares uma aplicação
> no Enable Banking (é gratuito para uso pessoal, mas tem burocracia).

---

## Porque é que isto não é trivial

Não existe um "abrir o ficheiro do meu banco". Desde 2018, a lei europeia
(**PSD2**) obriga os bancos a darem acesso aos teus dados **a aplicações que tu
autorizes**, através de uma API padronizada — é o chamado **Open Banking**. Mas:

- cada banco implementa a API à sua maneira;
- precisas do **consentimento** explícito do titular, que **caduca** (aqui, 90
  dias) e tem de ser renovado;
- há **limites apertados**: só umas poucas consultas automáticas por dia;
- a autenticação é forte (**SCA** — normalmente confirmação no telemóvel).

O `munny` esconde tudo isto atrás de um serviço chamado **Enable Banking**, que
fala com centenas de bancos por ti. Tu falas com o Enable Banking; ele fala com
os bancos.

## Como funciona, em três frases

1. Autorizas uma conta uma vez (és redirecionado para o site do banco, confirmas).
2. De 6 em 6 horas, o `munny` descarrega as transações novas e guarda-as numa
   base de dados local (SQLite).
3. Categoriza cada transação (regras simples + um modelo de IA para o resto) e
   mostra-te tudo num painel web e através de um "servidor MCP" (para
   assistentes de IA).

```
   O TEU BANCO
       │  (API Open Banking / PSD2)
       ▼
  Enable Banking  ──►  munny (app/sync.py)  ──►  SQLite  ──►  painel web  /dashboard
   (tradutor)          de 6 em 6 horas          .db local      servidor MCP  /mcp
```

## Conceitos (glossário)

| Termo | O que é |
|---|---|
| **Open Banking** | Acesso aos teus dados bancários por apps que autorizas, via API. |
| **PSD2** | A diretiva europeia que obriga os bancos a oferecerem esse acesso. |
| **ASPSP** | Chique para "o banco" (a instituição que guarda a tua conta). |
| **TPP** | A app terceira que acede aos dados — neste caso, o `munny` via Enable Banking. |
| **Consentimento** | A autorização que dás; tem prazo de validade e âmbito limitado. |
| **SCA** | Autenticação forte — confirmares que és tu (app do banco, SMS, biometria). |
| **Enable Banking** | O intermediário que normaliza a ligação a muitos bancos. |
| **Watermark** | A marca de "já sincronizei até aqui", para só pedir o que falta. |
| **Idempotência** | Correr a sincronização duas vezes não duplica transações. |
| **MCP** | *Model Context Protocol* — forma padrão de dar dados/ferramentas a assistentes de IA. |

## Stack

Python 3.12 · FastAPI · SQLite (biblioteca padrão, sem ORM) · APScheduler ·
MCP SDK · `google-genai` (Gemini) · Docker. Sem passo de *build* no frontend.

## Instalação

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
pytest            # deve passar tudo, sem precisares de credenciais
```

## Configuração

Editar o `.env`:

| Variável | Para quê | Como obter |
|---|---|---|
| `ENABLE_BANKING_APPLICATION_ID` | Identifica a tua aplicação | Registo em [enablebanking.com](https://enablebanking.com/) |
| `ENABLE_BANKING_PRIVATE_KEY_B64` | Assina os pedidos (chave RSA em base64) | Gerada no registo; `base64 -w0 private.pem` |
| `ENABLE_BANKING_REDIRECT_URL` | Para onde o banco te devolve após autorizares | Registas o mesmo URL no painel do Enable Banking |
| `MCP_BEARER_TOKEN` | Segredo único: password do painel **e** do servidor MCP | Inventa uma string longa e aleatória |
| `GEMINI_API_KEY` | Categorização por IA do que as regras não apanham | [aistudio.google.com](https://aistudio.google.com/apikey) |
| `DB_PATH` | Onde fica o ficheiro SQLite | Opcional (default `./data/trocado.db`) |

Os bancos a ligar definem-se em `ENABLE_BANKING_ASPSPS` (`app/config.py`).

## Correr

```bash
uvicorn app.main:app --reload
```

| Endpoint | O que faz |
|---|---|
| `GET /connect/enable-banking/start/{aspsp_key}` | Começa a autorização de um banco (redireciona-te para lá) |
| `GET /dashboard` | O painel (entra com o `MCP_BEARER_TOKEN`) |
| `GET /dashboard/relatorios` | Os relatórios semanais gerados pelo agente |
| `GET /dashboard/obrigacoes` | Registar despesas sazonais (IUC, seguro, IRS…) |
| `GET /status` | Quantos dias faltam até o consentimento caducar |
| `POST /mcp` | Servidor MCP (header `Authorization: Bearer <MCP_BEARER_TOKEN>`) |

## Agente financeiro semanal

O munny faz as **contas**; um **agente de IA agendado** faz a **narrativa**. Não há
framework de agente — é uma *skill* (`.claude/skills/weekly-finance-report/`) que
uma rotina agendada corre todas as segundas:

1. chama as ferramentas MCP `weekly_summary`, `recurring_payments` e
   `upcoming_obligations` (o munny calcula tudo em `app/insights.py`);
2. escreve um relatório em português — resumo da semana e comparação com as
   anteriores, subscrições e gastos fixos, pagamentos sazonais a caminho com
   quanto pôr de lado por mês (*sinking fund*), e um conceito de educação
   financeira ligado a um número real;
3. guarda-o via `save_weekly_report` (aparece em `/dashboard/relatorios`) e
   envia-o por email.

É um exemplo de como o servidor MCP transforma a base de dados numa **interface
para um agente**. A configuração da rotina (quando corre, com que credenciais,
para que email) vive fora do repositório.

## Deploy

- **Railway** — o `Dockerfile` corre `uvicorn` na porta `$PORT`. Define as
  variáveis no painel e monta um volume persistente em `DB_PATH`. O agendador é
  interno, por isso corre **só 1 réplica**.
- **Servidor próprio** — `docker-compose.yml` + `Caddyfile` (proxy com HTTPS
  automático); muda o domínio no `Caddyfile`.

## Aprender com este repo

- [`docs/COMO-FUNCIONA.md`](docs/COMO-FUNCIONA.md) — o guia completo: Open Banking
  explicado, e o código percorrido passo a passo.
- O código está comentado em português. Comentários marcados `ponytail:` assinalam
  simplificações deliberadas e o seu limite (ex.: "só funciona com 1 réplica").
- Os testes (`tests/`) não precisam de credenciais nem de internet — são um bom
  ponto de partida para perceber o comportamento esperado.

## Estrutura

```
app/
  main.py            aplicação FastAPI: autenticação, arranque (agendador + MCP)
  config.py          variáveis de ambiente e constantes
  db.py              schema SQLite e funções de consulta (sem ORM)
  sources/
    enable_banking.py  cliente da API Enable Banking
  sync.py            orquestra a sincronização periódica
  categorize.py      regras por palavra-chave + recurso a IA
  categories.yaml    as palavras-chave das regras
  insights.py        resumo semanal, gastos recorrentes, fundo de reserva
  mcp_server.py      as ferramentas expostas via MCP
  dashboard.py       as páginas do painel
  auth.py            o segredo partilhado e o cookie de sessão
  templates/         as páginas HTML (Jinja2)
.claude/skills/      a skill do agente financeiro semanal
docs/                material educativo
tests/               pytest, sem dependências externas
```

## Limitações (de propósito)

- **SQLite, um único processo, uma réplica.** Chega para uso pessoal; não escala
  horizontalmente sem trocar de base de dados e mover o agendador para fora.
- **~4 sincronizações automáticas por dia por banco** — limite da PSD2. Fora
  disso, tens de reautorizar.
- **A categorização por IA custa dinheiro** (chamadas ao Gemini) e pode errar —
  daí o botão para corrigir à mão.
- **Consentimento caduca aos 90 dias.** `GET /status` avisa; renovar é manual.
- **Sazonalidade de gastos** ("gastas mais em dezembro") precisa de 12+ meses de
  histórico — fica para quando houver dados.
- **O agente não dá conselhos de investimento** — descreve os teus números e
  ensina conceitos; aconselhamento financeiro personalizado é atividade regulada.

## Licença

MIT — ver [LICENSE](LICENSE).
