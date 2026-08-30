import os
import tempfile
from datetime import date

import pytest

from app import db, insights, report


class _FakeGemini:
    def __init__(self, text):
        self._text = text
        self.models = self

    def generate_content(self, model, contents):
        self._last_prompt = contents
        return type("R", (), {"text": self._text})()


@pytest.fixture
def conn(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    monkeypatch.setattr(insights, "_lisbon_today", lambda: date(2026, 8, 28))
    with db.get_conn(path) as c:
        db.upsert_account(c, source="enable_banking", aspsp_key="wise", stable_key="BE1",
                          provider_ref="w", currency="EUR", display_name="Wise")
        aid = c.execute("SELECT id FROM accounts LIMIT 1").fetchone()["id"]
        db.insert_transactions_idempotent(c, aid, "enable_banking", [
            {"external_id": "t1", "booking_date": "2026-08-18", "amount": -12.0, "currency": "EUR", "description": "x"},
        ])
        yield c
    os.unlink(path)


def test_generates_saves_and_skips_email_without_key(conn, monkeypatch):
    fake = _FakeGemini("# Relatório\n\nOlá.")
    monkeypatch.setattr(report.genai, "Client", lambda: fake)
    monkeypatch.setattr(report, "RESEND_API_KEY", "")
    sent = []
    monkeypatch.setattr(report.httpx, "post", lambda *a, **k: sent.append(1))

    ws = report.generate_weekly_report(conn, "2026-08-17")

    assert ws == "2026-08-17"
    assert db.get_report(conn, "2026-08-17")["body_md"] == "# Relatório\n\nOlá."
    assert sent == []  # sem RESEND_API_KEY não envia
    assert "resumo_semanal" in fake._last_prompt and "2026-08-17" in fake._last_prompt


def test_sends_email_when_configured(conn, monkeypatch):
    monkeypatch.setattr(report.genai, "Client", lambda: _FakeGemini("# R"))
    monkeypatch.setattr(report, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(report, "REPORT_EMAIL_TO", "eu@exemplo.pt")
    calls = {}

    def fake_post(url, headers, json, timeout):
        calls.update(url=url, json=json)
        return type("R", (), {"raise_for_status": lambda self: None})()

    monkeypatch.setattr(report.httpx, "post", fake_post)
    report.generate_weekly_report(conn, "2026-08-17")

    assert calls["url"] == "https://api.resend.com/emails"
    assert calls["json"]["to"] == "eu@exemplo.pt"
    assert "<h1>" in calls["json"]["html"]


def test_concept_rotation_is_deterministic():
    assert len(report.CONCEITOS) == 12
    w = date(2026, 8, 17).isocalendar().week
    assert report.CONCEITOS[w % 12] == report.CONCEITOS[w % 12]
