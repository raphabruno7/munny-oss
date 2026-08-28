"""Segredo partilhado e cookie de sessão do dashboard.

O mesmo `MCP_BEARER_TOKEN` serve de password do dashboard. Falha fechada: se o
segredo estiver vazio, nenhum login nem pedido MCP passa.
"""
import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

COOKIE_NAME = "trocado_session"
SESSION_TTL = timedelta(days=30)


def secret() -> str:
    return os.environ.get("MCP_BEARER_TOKEN", "")


def _sign(payload: str) -> str:
    return hmac.new(secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def sign_session(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    expiry = (now + SESSION_TTL).isoformat()
    payload = base64.urlsafe_b64encode(expiry.encode()).decode()
    return f"{payload}.{_sign(payload)}"


def verify_session(cookie: str | None) -> bool:
    if not secret() or not cookie or "." not in cookie:
        return False
    payload, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    try:
        expiry = datetime.fromisoformat(base64.urlsafe_b64decode(payload).decode())
    except (ValueError, TypeError):
        return False
    return datetime.now(timezone.utc) < expiry


def check_password(password: str) -> bool:
    s = secret()
    return bool(s) and hmac.compare_digest(password, s)
