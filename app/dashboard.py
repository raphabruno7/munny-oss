"""Dashboard web: saldos, extrato do mês e gastos por categoria.

Páginas server-rendered no mesmo app FastAPI. HTMX só para a edição inline de
categoria; os filtros de data/banco são um GET form normal.
"""
from datetime import date
from pathlib import Path

import markdown as md
from fastapi import APIRouter, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import auth, db, insights
from app.categorize import valid_categories
from app.config import ENABLE_BANKING_ASPSPS
from app.sync import _categorize_pending

router = APIRouter(prefix="/dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _require_session(request: Request) -> RedirectResponse | None:
    if not auth.verify_session(request.cookies.get(auth.COOKIE_NAME)):
        return RedirectResponse("/dashboard/login", status_code=303)
    return None


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, password: str = Form("")):
    if not auth.check_password(password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Password errada."}, status_code=401
        )
    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie(
        auth.COOKIE_NAME,
        auth.sign_session(),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=int(auth.SESSION_TTL.total_seconds()),
    )
    return resp


@router.get("", response_class=HTMLResponse)
def dashboard(
    request: Request,
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    aspsp: str | None = None,
):
    guard = _require_session(request)
    if guard:
        return guard

    today = date.today()
    date_from = date_from or today.replace(day=1).isoformat()
    date_to = date_to or today.isoformat()
    aspsp_key = aspsp or None

    with db.get_conn() as conn:
        accounts = [dict(r) for r in db.list_accounts(conn, source="enable_banking")]
        txns = [dict(r) for r in db.query_transactions(conn, date_from, date_to, aspsp_key=aspsp_key)]

    spending: dict[str, float] = {}
    for t in txns:
        if t["amount"] < 0:
            cat = t["category"] or "sem categoria"
            spending[cat] = spending.get(cat, 0.0) + abs(t["amount"])

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "accounts": accounts,
            "transactions": list(reversed(txns)),
            "spending": dict(sorted(spending.items(), key=lambda kv: kv[1], reverse=True)),
            "date_from": date_from,
            "date_to": date_to,
            "aspsp": aspsp_key,
            "aspsps": ENABLE_BANKING_ASPSPS,
            "categories": valid_categories(),
        },
    )


@router.post("/transactions/{txn_id}/category", response_class=HTMLResponse)
def edit_category(request: Request, txn_id: int, category: str = Form(...)):
    guard = _require_session(request)
    if guard:
        return guard
    if category not in valid_categories():
        return Response("categoria inválida", status_code=400)
    with db.get_conn() as conn:
        db.set_category(conn, txn_id, category, "manual", rationale="editado à mão")
    return templates.TemplateResponse(
        request,
        "_category_cell.html",
        {
            "txn": {"id": txn_id, "category": category, "category_source": "manual",
                    "category_rationale": "editado à mão"},
            "categories": valid_categories(),
        },
    )


@router.post("/recategorize")
def recategorize(request: Request):
    guard = _require_session(request)
    if guard:
        return guard
    with db.get_conn() as conn:
        db.reset_auto_categories(conn)
        _categorize_pending(conn)
    return RedirectResponse("/dashboard", status_code=303)


_MESES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
          "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


@router.get("/obrigacoes", response_class=HTMLResponse)
def obligations_page(request: Request):
    guard = _require_session(request)
    if guard:
        return guard
    with db.get_conn() as conn:
        obligations = [dict(r) for r in db.list_obligations(conn)]
        upcoming = insights.upcoming_obligations(conn, horizonte_meses=12)
    return templates.TemplateResponse(
        request, "obrigacoes.html",
        {"obligations": obligations, "upcoming": upcoming, "meses": _MESES},
    )


@router.post("/obrigacoes")
def add_obligation(request: Request, name: str = Form(...), amount: float = Form(...),
                   due_month: int = Form(...), recurrence: str = Form("annual"),
                   notes: str = Form("")):
    guard = _require_session(request)
    if guard:
        return guard
    if not (1 <= due_month <= 12) or amount <= 0 or not name.strip():
        return Response("dados inválidos", status_code=400)
    with db.get_conn() as conn:
        db.add_obligation(conn, name=name.strip(), amount=amount, due_month=due_month,
                          recurrence=recurrence, notes=notes.strip() or None)
    return RedirectResponse("/dashboard/obrigacoes", status_code=303)


@router.post("/obrigacoes/{obligation_id}/delete")
def remove_obligation(request: Request, obligation_id: int):
    guard = _require_session(request)
    if guard:
        return guard
    with db.get_conn() as conn:
        db.delete_obligation(conn, obligation_id)
    return RedirectResponse("/dashboard/obrigacoes", status_code=303)


@router.get("/relatorios", response_class=HTMLResponse)
def reports_page(request: Request):
    guard = _require_session(request)
    if guard:
        return guard
    with db.get_conn() as conn:
        reports = [dict(r) for r in db.list_reports(conn)]
    return templates.TemplateResponse(request, "relatorios.html", {"reports": reports})


@router.get("/relatorios/{week_start}", response_class=HTMLResponse)
def report_page(request: Request, week_start: str):
    guard = _require_session(request)
    if guard:
        return guard
    with db.get_conn() as conn:
        report = db.get_report(conn, week_start)
    if report is None:
        return Response("relatório não encontrado", status_code=404)
    body_html = md.markdown(report["body_md"], extensions=["tables", "sane_lists"])
    return templates.TemplateResponse(
        request, "relatorio.html", {"week_start": week_start, "body_html": body_html},
    )
