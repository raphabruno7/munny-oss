import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app import auth, db


@pytest.fixture
def client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setenv("MCP_BEARER_TOKEN", "segredo")

    from app.main import app

    # TestClient sem `with`: entrar no contexto corre o lifespan (APScheduler + sync real).
    yield TestClient(app, follow_redirects=False)
    os.unlink(path)


def _login(client, password="segredo"):
    return client.post("/dashboard/login", data={"password": password})


def test_no_cookie_is_rejected_everywhere(client):
    assert client.get("/dashboard").status_code == 303
    assert client.post("/dashboard/transactions/1/category", data={"category": "outros"}).status_code == 303
    assert client.post("/dashboard/recategorize").status_code == 303


def test_reset_auto_categories_keeps_manual_edits():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    try:
        with db.get_conn(path) as conn:
            aid = db.upsert_account(conn, source="enable_banking", aspsp_key="wise", stable_key="BE1",
                                    provider_ref="w", currency="EUR", display_name="Wise")
            db.insert_transactions_idempotent(conn, aid, "enable_banking", [
                {"external_id": "a", "booking_date": "2026-08-01", "amount": -1.0, "currency": "EUR", "description": "x"},
                {"external_id": "b", "booking_date": "2026-08-02", "amount": -2.0, "currency": "EUR", "description": "y"},
            ])
            rows = conn.execute("SELECT id FROM transactions ORDER BY external_id").fetchall()
            db.set_category(conn, rows[0]["id"], "compras", "llm", rationale="loja")
            db.set_category(conn, rows[1]["id"], "saude", "manual", rationale="editado à mão")

            db.reset_auto_categories(conn)

            after = {r["external_id"]: r for r in conn.execute("SELECT * FROM transactions").fetchall()}
        assert after["a"]["category"] is None
        assert after["b"]["category"] == "saude"
        assert after["b"]["category_source"] == "manual"
    finally:
        os.unlink(path)


def test_wrong_password_rejected(client):
    resp = _login(client, "errada")
    assert resp.status_code == 401
    assert auth.COOKIE_NAME not in resp.cookies


def test_correct_password_grants_access(client):
    resp = _login(client)
    assert resp.status_code == 303
    cookie = resp.cookies.get(auth.COOKIE_NAME)
    assert cookie
    page = client.get("/dashboard", cookies={auth.COOKIE_NAME: cookie})
    assert page.status_code == 200


def test_empty_secret_denies_login(client, monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "")
    assert _login(client, "").status_code == 401
    assert _login(client, "qualquer").status_code == 401


def test_query_transactions_filters_by_aspsp():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    try:
        with db.get_conn(path) as conn:
            wise = db.upsert_account(conn, source="enable_banking", aspsp_key="wise",
                                     stable_key="BE1", provider_ref="w", currency="EUR", display_name="Wise")
            nb = db.upsert_account(conn, source="enable_banking", aspsp_key="novo-banco",
                                   stable_key="PT1", provider_ref="n", currency="EUR", display_name="NB")
            db.insert_transactions_idempotent(conn, wise, "enable_banking", [
                {"external_id": "w1", "booking_date": "2026-08-10", "amount": -5.0, "currency": "EUR", "description": "a"}])
            db.insert_transactions_idempotent(conn, nb, "enable_banking", [
                {"external_id": "n1", "booking_date": "2026-08-11", "amount": -9.0, "currency": "EUR", "description": "b"}])

            all_rows = db.query_transactions(conn, "2026-08-01", "2026-08-31")
            wise_rows = db.query_transactions(conn, "2026-08-01", "2026-08-31", aspsp_key="wise")

        assert len(all_rows) == 2
        assert [r["external_id"] for r in wise_rows] == ["w1"]
        assert wise_rows[0]["aspsp_key"] == "wise"
    finally:
        os.unlink(path)
