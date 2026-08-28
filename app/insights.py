"""Análise dos dados já guardados: resumo semanal, deteção de gastos recorrentes,
fundo de reserva para obrigações sazonais. Funções puras sobre a BD, sem rede."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import db
from app.categorize import merchant_text

LISBON = ZoneInfo("Europe/Lisbon")


def _lisbon_today() -> date:
    return datetime.now(LISBON).date()


def week_bounds(ref_date: date | None = None) -> tuple[date, date]:
    """Última semana **completa** (segunda a domingo) antes de `ref_date` (hoje por defeito)."""
    today = ref_date or _lisbon_today()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    return last_monday, last_monday + timedelta(days=6)


def _aggregate(conn, start: date, end: date) -> dict:
    rows = db.query_transactions(conn, start.isoformat(), end.isoformat())
    saidas = [r for r in rows if r["amount"] < 0]
    entradas = [r for r in rows if r["amount"] > 0]
    por_categoria: dict[str, float] = {}
    for r in saidas:
        cat = r["category"] or "sem categoria"
        por_categoria[cat] = round(por_categoria.get(cat, 0.0) + abs(r["amount"]), 2)
    maior = min(saidas, key=lambda r: r["amount"], default=None)
    return {
        "total_saidas": round(sum(abs(r["amount"]) for r in saidas), 2),
        "total_entradas": round(sum(r["amount"] for r in entradas), 2),
        "por_categoria": por_categoria,
        "maior_transacao": (
            {"descricao": maior["description"], "valor": maior["amount"],
             "data": maior["booking_date"]} if maior else None
        ),
        "n_transacoes": len(saidas),
    }


def weekly_summary(conn, week_start: str | date | None = None) -> dict:
    if week_start is None:
        ws, we = week_bounds()
    else:
        ws = date.fromisoformat(week_start) if isinstance(week_start, str) else week_start
        we = ws + timedelta(days=6)

    atual = _aggregate(conn, ws, we)
    anterior = _aggregate(conn, ws - timedelta(days=7), we - timedelta(days=7))
    quatro = [
        _aggregate(conn, ws - timedelta(days=7 * k), we - timedelta(days=7 * k))["total_saidas"]
        for k in range(1, 5)
    ]
    media_4 = round(sum(quatro) / 4, 2)

    em_alta = sorted(
        (
            {"categoria": cat, "delta": round(v - anterior["por_categoria"].get(cat, 0.0), 2)}
            for cat, v in atual["por_categoria"].items()
            if v > anterior["por_categoria"].get(cat, 0.0)
        ),
        key=lambda d: d["delta"], reverse=True,
    )

    return {
        "periodo": {"de": ws.isoformat(), "ate": we.isoformat()},
        **atual,
        "vs_semana_anterior": round(atual["total_saidas"] - anterior["total_saidas"], 2),
        "vs_media_4_semanas": round(atual["total_saidas"] - media_4, 2),
        "media_4_semanas": media_4,
        "categorias_em_alta": em_alta,
    }


def _norm_key(row) -> str:
    return merchant_text(dict(row)).lower().strip()[:30]


def detect_recurring(conn, meses: int = 6) -> list[dict]:
    """Gastos que se repetem quase todos os meses com valor parecido.
    ponytail: heurística simples (mediana ±20%, >=3 meses); só cadência mensal — anual (seguros) fica para v2."""
    end = _lisbon_today()
    start = end - timedelta(days=31 * meses)
    grupos: dict[str, list] = {}
    for r in db.query_transactions(conn, start.isoformat(), end.isoformat()):
        key = _norm_key(r)
        if r["amount"] >= 0 or not key:
            continue
        grupos.setdefault(key, []).append(r)

    out = []
    for txns in grupos.values():
        meses_vistos = {t["booking_date"][:7] for t in txns}
        if len(meses_vistos) < 3:
            continue
        valores = sorted(abs(t["amount"]) for t in txns)
        mediana = valores[len(valores) // 2]
        if mediana == 0 or any(abs(v - mediana) / mediana > 0.20 for v in valores):
            continue
        ultima = max(txns, key=lambda t: t["booking_date"])
        out.append({
            "descricao": ultima["description"],
            "categoria": ultima["category"],
            "valor_mediano": round(mediana, 2),
            "ocorrencias": len(txns),
            "ultima_vez": ultima["booking_date"],
            "equivalente_mensal": round(mediana, 2),
        })
    return sorted(out, key=lambda d: d["equivalente_mensal"], reverse=True)


def _next_occurrence(today: date, due_month: int) -> date:
    year = today.year if due_month >= today.month else today.year + 1
    return date(year, due_month, 1)


def upcoming_obligations(conn, horizonte_meses: int = 6) -> list[dict]:
    """Para cada obrigação: quando vence e quanto pôr de lado por mês até lá."""
    today = _lisbon_today()
    out = []
    for o in db.list_obligations(conn):
        vence = _next_occurrence(today, o["due_month"])
        meses = (vence.year - today.year) * 12 + (vence.month - today.month)
        if meses > horizonte_meses:
            continue
        out.append({
            "nome": o["name"],
            "valor": round(o["amount"], 2),
            "vence_em": vence.isoformat(),
            "meses_ate_vencer": meses,
            "por_de_lado_por_mes": round(o["amount"] / max(1, meses), 2),
            "vence_este_mes": meses == 0,
            "notas": o["notes"],
        })
    return sorted(out, key=lambda d: d["vence_em"])
