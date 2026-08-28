import hashlib
import logging
from datetime import datetime, timedelta, timezone

from app import db
from app.categorize import categorize_llm_fallback, categorize_rule, merchant_text
from app.config import UNATTENDED_SYNC_DAILY_LIMIT
from app.sources import enable_banking

LOOKBACK_MARGIN_DAYS = 2

logger = logging.getLogger("trocado.sync")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _synthetic_external_id(account_id: int, txn: dict) -> str:
    key = f"{account_id}|{txn['booking_date']}|{txn['amount']}|{txn['currency']}|{txn.get('description')}"
    return hashlib.sha256(key.encode()).hexdigest()


def sync_enable_banking() -> None:
    with db.get_conn() as conn:
        accounts_by_aspsp: dict[str, list] = {}
        for account in db.list_accounts(conn, source="enable_banking"):
            accounts_by_aspsp.setdefault(account["aspsp_key"], []).append(account)

        for aspsp_key, accounts in accounts_by_aspsp.items():
            if not db.check_and_increment_unattended_quota(conn, "enable_banking", aspsp_key, UNATTENDED_SYNC_DAILY_LIMIT):
                logger.warning(
                    "Enable Banking (%s): limite de %s pedidos/dia atingido, a saltar esta corrida",
                    aspsp_key, UNATTENDED_SYNC_DAILY_LIMIT,
                )
                continue

            for account in accounts:
                since = account["last_synced_at"]
                date_from = (
                    (datetime.fromisoformat(since) - timedelta(days=LOOKBACK_MARGIN_DAYS)).date().isoformat()
                    if since else (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
                )
                date_to = datetime.now(timezone.utc).date().isoformat()

                raw_txns = enable_banking.get_transactions(account["provider_ref"], date_from, date_to)
                txns = enable_banking.to_ledger_transactions(raw_txns)
                for txn in txns:
                    if not txn["external_id"]:
                        txn["external_id"] = _synthetic_external_id(account["id"], txn)
                db.insert_transactions_idempotent(conn, account["id"], "enable_banking", txns)
                db.update_watermark(conn, account["id"], datetime.now(timezone.utc).isoformat())

                try:
                    balance = enable_banking.pick_balance(enable_banking.get_balances(account["provider_ref"]))
                    if balance is not None:
                        db.update_balance(conn, account["id"], balance, datetime.now(timezone.utc).isoformat())
                except Exception:
                    logger.exception("get_balances falhou para a conta %s", account["id"])

        _categorize_pending(conn)


def _categorize_pending(conn) -> None:
    pending = [dict(r) for r in db.uncategorized_transactions(conn)]
    llm_batch = []
    for txn in pending:
        match = categorize_rule(txn["description"])
        if match:
            category, keyword = match
            db.set_category(conn, txn["id"], category, "rule", rationale=f"regra: «{keyword}»")
        else:
            llm_batch.append({"id": txn["id"], "text": merchant_text(txn)})

    if llm_batch:
        try:
            results = categorize_llm_fallback(llm_batch)
        except Exception:
            logger.exception("categorize_llm_fallback falhou, transações ficam por categorizar")
            return
        for txn_id, (category, rationale) in results.items():
            db.set_category(conn, txn_id, category, "llm", rationale=rationale)


def sync_all() -> None:
    sync_enable_banking()
