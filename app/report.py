"""Relatório financeiro semanal: junta os números do `insights`, pede ao Gemini
para os narrar em português, grava no dashboard e envia por email (Resend).

Corre uma vez por semana (job no APScheduler de `app/main.py`)."""
import datetime
import json
import logging

import httpx
import markdown as md
from google import genai

from app import db, insights
from app.config import REPORT_EMAIL_FROM, REPORT_EMAIL_TO, RESEND_API_KEY

logger = logging.getLogger("trocado.report")

CONCEITOS = [
    ("Fundo de emergência", "3 a 6 meses de gastos fixos guardados e líquidos."),
    ("Regra 50/30/20", "necessidades / desejos / poupança."),
    ("Sinking funds", "poupar aos poucos para despesas grandes previsíveis."),
    ("Juro composto", "juros sobre juros; o tempo é o fator dominante."),
    ("Taxa de poupança", "a percentagem do que entra que não gastas."),
    ("Inflação", "dinheiro parado perde poder de compra."),
    ("Custo de oportunidade", "cada euro gasto é um euro que não faz outra coisa."),
    ("Lifestyle creep", "os gastos sobem com o rendimento sem darmos conta."),
    ("Rever subscrições", "cancelar o que não se usa há 60 dias."),
    ("Poupança vs investimento", "liquidez e risco diferentes; para que serve cada um."),
    ('O "número" dos gastos fixos', "quanto precisas por mês só para as contas."),
    ("Almofada de liquidez", "dinheiro à mão para não recorrer a crédito caro."),
]

_PROMPT = """És o agente financeiro semanal de uma app de finanças pessoais em Portugal.
Escreve um relatório em **português europeu**, em markdown, com esta estrutura exata:

# Semana de {de} a {ate}

## Resumo
- Total gasto, número de transações.
- Comparação com a semana anterior e com a média das 4 semanas anteriores (diz se subiu ou desceu e quanto).
- Entradas na semana.

## Por categoria
Tabela: categoria | valor | variação vs semana anterior. Ordena por valor. Se houver
categorias em alta, uma frase a destacar a que mais subiu.

## Destaque
O maior gasto da semana.

## Gastos fixos e subscrições
Tabela dos pagamentos recorrentes com o equivalente mensal. Soma o total fixo mensal estimado.

## Pagamentos a caminho
Para cada obrigação: valor, data, e **quanto pôr de lado por mês** até lá. Marca as que vencem este mês.
Se a lista vier vazia: "Nada nos próximos 6 meses."

## Conceito da semana: {conceito_titulo}
2 a 4 frases sobre este conceito ({conceito_desc}), SEMPRE ligado a um número real deste relatório.

Regras de tom: descreve números e padrões, explica conceitos. NUNCA recomendes produtos
financeiros, ações, cripto, corretoras ou estratégias de investimento — é atividade regulada.
Sem juízos de valor ("gastaste demais"); mostra o facto e a comparação, a decisão é do utilizador.

Dados (JSON):
{dados}
"""


def _resend_send(subject: str, html: str) -> None:
    if not RESEND_API_KEY or not REPORT_EMAIL_TO:
        logger.warning("RESEND_API_KEY/REPORT_EMAIL_TO em falta — relatório gravado mas não enviado")
        return
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={"from": REPORT_EMAIL_FROM, "to": REPORT_EMAIL_TO, "subject": subject, "html": html},
        timeout=20,
    )
    resp.raise_for_status()


def _email_html(body_md: str) -> str:
    inner = md.markdown(body_md, extensions=["tables", "sane_lists"])
    return (
        '<div style="font-family:sans-serif;max-width:640px;margin:0 auto;color:#1e293b;line-height:1.6">'
        f"{inner}</div>"
    )


def generate_weekly_report(conn, week_start: str | None = None) -> str:
    """Gera, grava e envia o relatório da última semana completa. Devolve o week_start."""
    if week_start is None:
        week_start = insights.week_bounds()[0].isoformat()
    ws = datetime.date.fromisoformat(week_start)

    summary = insights.weekly_summary(conn, week_start)
    dados = {
        "resumo_semanal": summary,
        "gastos_recorrentes": insights.detect_recurring(conn),
        "obrigacoes_a_caminho": insights.upcoming_obligations(conn, horizonte_meses=6),
    }
    titulo, desc = CONCEITOS[ws.isocalendar().week % len(CONCEITOS)]
    prompt = _PROMPT.format(
        de=summary["periodo"]["de"], ate=summary["periodo"]["ate"],
        conceito_titulo=titulo, conceito_desc=desc,
        dados=json.dumps(dados, ensure_ascii=False, indent=2),
    )

    response = genai.Client().models.generate_content(model="gemini-3.6-flash", contents=prompt)
    body_md = response.text.strip()

    db.save_report(conn, week_start, body_md)
    try:
        _resend_send(f"munny — semana de {week_start}", _email_html(body_md))
    except Exception:
        logger.exception("envio do relatório por email falhou (relatório na mesma gravado)")
    return week_start
