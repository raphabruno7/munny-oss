import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt

from app.config import ENABLE_BANKING_API_BASE, ENABLE_BANKING_APPLICATION_ID, enable_banking_private_key


def build_jwt() -> str:
    now = int(time.time())
    payload = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + 3600}
    return jwt.encode(
        payload, enable_banking_private_key(), algorithm="RS256",
        headers={"kid": ENABLE_BANKING_APPLICATION_ID},
    )


def _headers() -> dict:
    return {"Authorization": f"Bearer {build_jwt()}"}


def start_auth(aspsp_name: str, aspsp_country: str, redirect_url: str, state: str | None = None) -> tuple[str, str]:
    """Devolve (auth_url, state) — state serve para correlacionar o callback."""
    state = state or str(uuid.uuid4())
    valid_until = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    resp = httpx.post(
        f"{ENABLE_BANKING_API_BASE}/auth",
        headers=_headers(),
        json={
            "access": {"valid_until": valid_until},
            "aspsp": {"name": aspsp_name, "country": aspsp_country},
            "state": state,
            "redirect_url": redirect_url,
            "psu_type": "personal",
        },
    )
    resp.raise_for_status()
    return resp.json()["url"], state


def exchange_code_for_session(code: str) -> dict:
    """Troca o code do callback por session_id + lista de account uids."""
    resp = httpx.post(f"{ENABLE_BANKING_API_BASE}/sessions", headers=_headers(), json={"code": code})
    resp.raise_for_status()
    return resp.json()


def get_account_details(account_uid: str) -> dict:
    resp = httpx.get(f"{ENABLE_BANKING_API_BASE}/accounts/{account_uid}/details", headers=_headers())
    resp.raise_for_status()
    return resp.json()


def get_balances(account_uid: str) -> list[dict]:
    resp = httpx.get(f"{ENABLE_BANKING_API_BASE}/accounts/{account_uid}/balances", headers=_headers())
    resp.raise_for_status()
    return resp.json().get("balances", [])


def pick_balance(balances: list[dict]) -> float | None:
    """Escolhe o saldo mais relevante: disponível (ITAV) > fechado contabilístico (CLBD) > primeiro."""
    if not balances:
        return None
    by_type = {b.get("balance_type"): b for b in balances}
    chosen = by_type.get("ITAV") or by_type.get("CLBD") or balances[0]
    try:
        return float(chosen["balance_amount"]["amount"])
    except (KeyError, TypeError, ValueError):
        return None


def get_transactions(account_uid: str, date_from: str, date_to: str) -> list[dict]:
    """Todas as transações 'BOOK' no intervalo [date_from, date_to] (YYYY-MM-DD), paginando por continuation_key.
    Se uma página de continuação falhar, devolve o que já foi obtido em vez de perder tudo
    (a próxima sync recupera o resto via watermark)."""
    transactions: list[dict] = []
    params = {"date_from": date_from, "date_to": date_to, "transaction_status": "BOOK"}
    url = f"{ENABLE_BANKING_API_BASE}/accounts/{account_uid}/transactions"
    while True:
        try:
            resp = httpx.get(url, headers=_headers(), params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            break
        data = resp.json()
        transactions.extend(data.get("transactions", []))
        continuation_key = data.get("continuation_key")
        if not continuation_key:
            break
        params = {**params, "continuation_key": continuation_key}
    return transactions


def to_ledger_transactions(raw_transactions: list[dict]) -> list[dict]:
    """Normaliza transações do Enable Banking para o formato genérico usado por app.sync.
    entry_reference é opcional no Enable Banking; quando ausente, o hash sintético
    fica a cargo de app.sync (aqui devolvemos None para essa chave)."""
    out = []
    for txn in raw_transactions:
        amount = txn["transaction_amount"]
        signed_amount = float(amount["amount"])
        if txn.get("credit_debit_indicator") == "DBIT":
            signed_amount = -abs(signed_amount)
        else:
            signed_amount = abs(signed_amount)
        out.append({
            "external_id": txn.get("entry_reference"),
            "booking_date": txn["booking_date"],
            "amount": signed_amount,
            "currency": amount["currency"],
            "description": " ".join(txn.get("remittance_information", [])) or None,
            "raw": txn,
        })
    return out
