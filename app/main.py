import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import db, sync
from app import auth
from app.config import CONSENT_EXPIRING_SOON_DAYS, ENABLE_BANKING_ASPSPS, ENABLE_BANKING_REDIRECT_URL
from app.dashboard import router as dashboard_router
from app.mcp_server import mcp
from app.sources import enable_banking

logger = logging.getLogger("trocado")

SYNC_INTERVAL_HOURS = 6


def _run_sync_job() -> None:
    try:
        sync.sync_all()
    except Exception:
        logger.exception("sync_all falhou")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # ponytail: scheduler in-process, só funciona com 1 réplica; migrar para Railway Cron se escalar
    scheduler = BackgroundScheduler()
    scheduler.add_job(_run_sync_job, "interval", hours=SYNC_INTERVAL_HOURS, next_run_time=datetime.now())
    scheduler.start()
    async with mcp.session_manager.run():
        yield
    scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def mcp_bearer_auth(request: Request, call_next):
    if request.url.path.startswith("/mcp"):
        header = request.headers.get("authorization", "")
        secret = auth.secret()
        if not secret or header != f"Bearer {secret}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


app.mount("/mcp", mcp.streamable_http_app())
app.include_router(dashboard_router)


@app.get("/status")
def status():
    with db.get_conn() as conn:
        result = {}
        for aspsp_key in ENABLE_BANKING_ASPSPS:
            conn_row = db.get_bank_connection(conn, "enable_banking", aspsp_key)
            if conn_row is None or conn_row["session_id"] is None:
                result[aspsp_key] = {"status": "not_connected"}
                continue

            valid_until = conn_row["valid_until"]
            days_left = None
            state = conn_row["status"]
            if valid_until:
                days_left = (datetime.fromisoformat(valid_until) - datetime.now(timezone.utc)).days
                state = "expired" if days_left < 0 else "expiring_soon" if days_left <= CONSENT_EXPIRING_SOON_DAYS else "active"
                if state == "expiring_soon":
                    logger.warning("%s consent expiring in %s days", aspsp_key, days_left)
            result[aspsp_key] = {"valid_until": valid_until, "days_left": days_left, "status": state}
        return result


@app.get("/connect/enable-banking/start/{aspsp_key}")
def enable_banking_start(aspsp_key: str):
    aspsp = ENABLE_BANKING_ASPSPS.get(aspsp_key)
    if aspsp is None:
        return JSONResponse({"error": f"aspsp desconhecido: {aspsp_key}"}, status_code=404)
    auth_url, _ = enable_banking.start_auth(
        aspsp["name"], aspsp["country"], ENABLE_BANKING_REDIRECT_URL, state=f"{aspsp_key}:{uuid4()}"
    )
    return RedirectResponse(auth_url)


@app.get("/connect/enable-banking/callback")
def enable_banking_callback(code: str, state: str | None = None):
    aspsp_key = (state or "").split(":", 1)[0]
    if aspsp_key not in ENABLE_BANKING_ASPSPS:
        return JSONResponse({"error": "state inválido ou aspsp desconhecido"}, status_code=400)

    session = enable_banking.exchange_code_for_session(code)
    valid_until = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()

    with db.get_conn() as conn:
        db.upsert_bank_connection(
            conn, source="enable_banking", aspsp_key=aspsp_key, session_id=session["session_id"], valid_until=valid_until
        )
        accounts_upserted = []
        for account in session.get("accounts", []):
            uid = account["uid"]
            details = enable_banking.get_account_details(uid)
            iban = details.get("account_id", {}).get("iban") or uid
            db.upsert_account(
                conn,
                source="enable_banking",
                aspsp_key=aspsp_key,
                stable_key=iban,
                provider_ref=uid,
                currency=details.get("currency", "EUR"),
                display_name=details.get("name"),
            )
            accounts_upserted.append(iban)

    return {"aspsp_key": aspsp_key, "session_id": session["session_id"], "accounts": accounts_upserted, "valid_until": valid_until}
