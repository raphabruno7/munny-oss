---
name: weekly-finance-report
description: Gera o relatório financeiro semanal do munny a partir do servidor MCP, guarda-o no dashboard e envia-o por email. Usar quando o utilizador (ou uma rotina agendada) pede o relatório semanal de gastos, ou "corre o agente financeiro".
---

# Relatório financeiro semanal

Produz **um** relatório por semana a partir dos dados que o munny já tem. O munny
faz as contas (ferramentas MCP); tu escreves a narrativa em **português europeu**,
guardas e envias.

## Pré-requisitos

- Servidor MCP do munny acessível (ferramentas `weekly_summary`, `recurring_payments`,
  `upcoming_obligations`, `save_weekly_report`).
- Gmail para o envio.

Se o MCP não responder, para e diz porquê — não inventes números.

## Passos

1. **Recolher os dados** (uma chamada a cada):
   - `weekly_summary()` — sem argumentos, usa a última semana completa. Guarda o
     `periodo.de` (segunda-feira, `YYYY-MM-DD`) — é a chave do relatório.
   - `recurring_payments()`
   - `upcoming_obligations(horizon_months=6)`

2. **Escrever o relatório** em markdown, com esta estrutura exata:

   ```
   # Semana de {de} a {ate}

   ## Resumo
   - Gastaste **{total_saidas} €** ({n_transacoes} transações).
   - {frase de comparação: vs semana anterior e vs média das 4 semanas —
      usa vs_semana_anterior e vs_media_4_semanas, diz se subiu ou desceu e quanto}
   - Entradas: {total_entradas} €.

   ## Por categoria
   {tabela: categoria | valor | vs semana anterior — ordenada por valor desc}
   {se houver categorias_em_alta, uma frase a destacar a que mais subiu}

   ## Destaque
   - Maior gasto: {maior_transacao.descricao}, {maior_transacao.valor} € ({maior_transacao.data}).

   ## Gastos fixos e subscrições
   {tabela de recurring_payments: descrição | ~valor/mês | categoria}
   - Total fixo mensal estimado: **{soma dos equivalente_mensal} €**.

   ## Pagamentos a caminho
   {para cada upcoming_obligations, uma linha:}
   - **{nome}** — {valor} € em {vence_em}. Põe **{por_de_lado_por_mes} €/mês** de lado
     ({meses_ate_vencer} meses). {se vence_este_mes: "⚠️ vence este mês."}
   {se a lista estiver vazia: "Nada nos próximos 6 meses."}

   ## Conceito da semana: {título}
   {2–4 frases. Escolhe UM conceito da lista abaixo, rodando (não repitas o da
    semana passada — vê o relatório anterior se precisares). Liga-o SEMPRE a um
    número real deste relatório.}
   ```

3. **Guardar**: `save_weekly_report(week_start="{de}", body_md="{o markdown completo}")`.

4. **Enviar por email** o mesmo conteúdo (assunto: `munny — semana de {de}`), em
   markdown ou HTML simples, para o utilizador.

5. Responder ao utilizador com um resumo de 2 linhas e o link
   `/dashboard/relatorios/{de}`.

## Lista rotativa de conceitos

1. **Fundo de emergência** — 3 a 6 meses de gastos fixos guardados e líquidos.
2. **Regra 50/30/20** — necessidades / desejos / poupança.
3. **Sinking funds** — poupar aos poucos para despesas grandes previsíveis.
4. **Juro composto** — juros sobre juros; o tempo é o fator dominante.
5. **Taxa de poupança** — % do que entra que não gastas; é o número que mais importa.
6. **Inflação** — dinheiro parado perde poder de compra.
7. **Custo de oportunidade** — todo o euro gasto é um euro que não faz outra coisa.
8. **Lifestyle creep** — os gastos sobem com o salário sem darmos conta.
9. **Rever subscrições** — cancelar o que não usas há 60 dias.
10. **Poupança vs investimento** — liquidez e risco diferentes; para que serve cada uma.
11. **O teu "número" de gastos fixos** — quanto precisas por mês só para as contas.
12. **Almofada de liquidez** — dinheiro à mão para não recorrer a crédito caro.

## Regras de tom

- Descreve **números e padrões**. Explica conceitos.
- **Nunca** recomendes produtos financeiros, ações, cripto, corretoras ou
  estratégias de investimento concretas — aconselhamento financeiro personalizado
  é atividade regulada.
- Sem juízos de valor sobre os gastos ("gastaste demasiado"). Mostra o facto e a
  comparação; a decisão é do utilizador.
- Português europeu.
