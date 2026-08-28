import os
import tempfile
from datetime import date

import pytest

from app import db, insights


@pytest.fixture
def conn(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    monkeypatch.setattr(insights, "_lisbon_today", lambda: date(2026, 8, 28))
    with db.get_conn(path) as c:
        db.upsert_account(
            c, source="enable_banking", aspsp_key="wise", stable_key="BE1",
            provider_ref="w", currency="EUR", display_name="Wise",
        )
        yield c
    os.unlink(path)


def _tx(conn, ext, day, amount, category=None, desc="x"):
    account_id = conn.execute("SELECT id FROM accounts LIMIT 1").fetchone()["id"]
    db.insert_transactions_idempotent(conn, account_id, "enable_banking", [
        {"external_id": ext, "booking_date": day, "amount": amount, "currency": "EUR", "description": desc},
    ])
    if category:
        row = conn.execute("SELECT id FROM transactions WHERE external_id = ?", (ext,)).fetchone()
        db.set_category(conn, row["id"], category, "rule")


def test_week_bounds_last_complete_week():
    # 2026-08-27 é quinta; a última semana completa é 17–23 de agosto
    assert insights.week_bounds(date(2026, 8, 27)) == (date(2026, 8, 17), date(2026, 8, 23))


def test_weekly_summary_aggregates_and_compares(conn):
    _tx(conn, "a", "2026-08-18", -10.0, "restaurantes")
    _tx(conn, "b", "2026-08-20", -20.0, "compras")
    _tx(conn, "c", "2026-08-19", 100.0)            # entrada
    _tx(conn, "d", "2026-08-12", -30.0, "compras")  # semana anterior
    _tx(conn, "e", "2026-08-05", -5.0, "compras")   # 2 semanas antes

    s = insights.weekly_summary(conn, "2026-08-17")
    assert s["periodo"] == {"de": "2026-08-17", "ate": "2026-08-23"}
    assert s["total_saidas"] == 30.0
    assert s["total_entradas"] == 100.0
    assert s["por_categoria"] == {"restaurantes": 10.0, "compras": 20.0}
    assert s["vs_semana_anterior"] == 0.0            # 30 esta semana, 30 na anterior
    assert s["media_4_semanas"] == pytest.approx(8.75)  # (30 + 5 + 0 + 0) / 4
    assert s["categorias_em_alta"][0]["categoria"] == "restaurantes"


def test_detect_recurring_finds_monthly_and_ignores_oneoff(conn):
    for i, m in enumerate(("06", "07", "08")):
        _tx(conn, f"nf{i}", f"2026-{m}-15", -12.99, "subscricoes", "NETFLIX")
    _tx(conn, "book", "2026-07-03", -40.0, "compras", "LIVRARIA LELLO")

    rec = insights.detect_recurring(conn, meses=6)
    nomes = [r["descricao"] for r in rec]
    assert "NETFLIX" in nomes
    assert "LIVRARIA LELLO" not in nomes
    assert rec[0]["equivalente_mensal"] == 12.99
    assert rec[0]["ocorrencias"] == 3


def test_upcoming_obligations_sinking_fund_math(conn):
    db.add_obligation(conn, name="Seguro carro", amount=300.0, due_month=10)   # daqui a 2 meses
    db.add_obligation(conn, name="IUC", amount=150.0, due_month=3)             # daqui a 7 meses
    db.add_obligation(conn, name="Algo agora", amount=90.0, due_month=8)       # este mês

    up = {o["nome"]: o for o in insights.upcoming_obligations(conn, horizonte_meses=12)}
    assert up["Seguro carro"]["meses_ate_vencer"] == 2
    assert up["Seguro carro"]["por_de_lado_por_mes"] == 150.0
    assert up["IUC"]["por_de_lado_por_mes"] == pytest.approx(21.43, abs=0.01)
    assert up["Algo agora"]["vence_este_mes"] is True
    assert up["Algo agora"]["por_de_lado_por_mes"] == 90.0

    # horizonte curto exclui a obrigação distante
    perto = [o["nome"] for o in insights.upcoming_obligations(conn, horizonte_meses=6)]
    assert "IUC" not in perto and "Seguro carro" in perto
