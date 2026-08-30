import base64
import os
from pathlib import Path

DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "trocado.db"))

ENABLE_BANKING_APPLICATION_ID = os.environ.get("ENABLE_BANKING_APPLICATION_ID", "")
ENABLE_BANKING_API_BASE = os.environ.get("ENABLE_BANKING_API_BASE", "https://api.enablebanking.com")
ENABLE_BANKING_REDIRECT_URL = os.environ.get("ENABLE_BANKING_REDIRECT_URL", "http://localhost:8000/connect/enable-banking/callback")

ENABLE_BANKING_ASPSPS = {
    "novo-banco": {"name": "Novo Banco", "country": "PT"},
    "wise": {"name": "Wise", "country": "BE"},
}

MCP_BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Relatório financeiro semanal por email (Resend). Sem RESEND_API_KEY, o job só
# grava o relatório no dashboard e não envia email.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
REPORT_EMAIL_FROM = os.environ.get("REPORT_EMAIL_FROM", "munny@example.com")
REPORT_EMAIL_TO = os.environ.get("REPORT_EMAIL_TO", "")

CONSENT_EXPIRING_SOON_DAYS = 7
UNATTENDED_SYNC_DAILY_LIMIT = 4


def _load_private_key_b64(env_var: str) -> bytes:
    value = os.environ.get(env_var, "")
    if not value:
        return b""
    return base64.b64decode(value)


def enable_banking_private_key() -> bytes:
    return _load_private_key_b64("ENABLE_BANKING_PRIVATE_KEY_B64")
