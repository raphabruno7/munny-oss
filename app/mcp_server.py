from mcp.server.mcpserver import MCPServer

from app import db
from app.config import ENABLE_BANKING_ASPSPS

mcp = MCPServer("trocado")


@mcp.tool()
def get_balance(aspsp_key: str | None = None) -> list[dict]:
    """Saldo atual por conta, opcionalmente filtrado por 'novo-banco' ou 'wise'."""
    with db.get_conn() as conn:
        rows = db.list_accounts(conn, source="enable_banking", aspsp_key=aspsp_key)
        return [dict(r) for r in rows]


@mcp.tool()
def list_transactions(date_from: str, date_to: str, source: str | None = None,
                       category: str | None = None) -> list[dict]:
    """Lista transações no período [date_from, date_to] (YYYY-MM-DD), com filtros opcionais."""
    with db.get_conn() as conn:
        rows = db.query_transactions(conn, date_from, date_to, source=source, category=category)
        return [dict(r) for r in rows]


@mcp.tool()
def spending_by_category(date_from: str, date_to: str) -> dict[str, float]:
    """Soma de gastos (valores negativos) por categoria no período."""
    with db.get_conn() as conn:
        rows = db.query_transactions(conn, date_from, date_to)
        totals: dict[str, float] = {}
        for r in rows:
            if r["amount"] >= 0:
                continue
            cat = r["category"] or "sem categoria"
            totals[cat] = totals.get(cat, 0.0) + abs(r["amount"])
        return totals


@mcp.tool()
def compare_sources(date_from: str, date_to: str) -> dict:
    """Compara gasto total por ASPSP (ex.: Novo Banco vs Wise) no período."""
    with db.get_conn() as conn:
        result = {}
        for aspsp_key in ENABLE_BANKING_ASPSPS:
            rows = db.query_transactions_by_aspsp(conn, date_from, date_to, aspsp_key)
            result[aspsp_key] = sum(abs(r["amount"]) for r in rows if r["amount"] < 0)
        return result


@mcp.tool()
def connection_status() -> dict:
    """Estado do consentimento PSD2 por ASPSP ligado via Enable Banking."""
    with db.get_conn() as conn:
        status = {}
        for aspsp_key in ENABLE_BANKING_ASPSPS:
            conn_row = db.get_bank_connection(conn, "enable_banking", aspsp_key)
            status[aspsp_key] = dict(conn_row) if conn_row else {"status": "not_connected"}
        return status
