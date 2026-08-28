import os
import tempfile

import pytest

from app import db


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    yield path
    os.unlink(path)


def test_duplicate_sync_does_not_duplicate_transactions(temp_db):
    with db.get_conn(temp_db) as conn:
        account_id = db.upsert_account(
            conn, source="enable_banking", aspsp_key="wise", stable_key="123:456",
            provider_ref="456", currency="EUR", display_name="EUR"
        )
        txns = [{
            "external_id": "txn-1", "booking_date": "2026-07-01",
            "amount": -10.5, "currency": "EUR", "description": "Continente",
        }]

        inserted_first = db.insert_transactions_idempotent(conn, account_id, "enable_banking", txns)
        inserted_second = db.insert_transactions_idempotent(conn, account_id, "enable_banking", txns)

        rows = conn.execute("SELECT * FROM transactions").fetchall()

    assert inserted_first == 1
    assert inserted_second == 0
    assert len(rows) == 1


def test_reauth_does_not_duplicate_account(temp_db):
    with db.get_conn(temp_db) as conn:
        id1 = db.upsert_account(
            conn, source="enable_banking", aspsp_key="novo-banco", stable_key="PT50000000000000000000000",
            provider_ref="uid-old", currency="EUR", display_name="Conta"
        )
        id2 = db.upsert_account(
            conn, source="enable_banking", aspsp_key="novo-banco", stable_key="PT50000000000000000000000",
            provider_ref="uid-new", currency="EUR", display_name="Conta"
        )
        accounts = conn.execute("SELECT * FROM accounts").fetchall()

    assert id1 == id2
    assert len(accounts) == 1
    assert accounts[0]["provider_ref"] == "uid-new"


def test_unattended_quota_blocks_after_limit(temp_db):
    with db.get_conn(temp_db) as conn:
        results = [db.check_and_increment_unattended_quota(conn, "enable_banking", "novo-banco", 4) for _ in range(5)]

    assert results == [True, True, True, True, False]


def test_quota_is_independent_per_aspsp(temp_db):
    with db.get_conn(temp_db) as conn:
        for _ in range(4):
            assert db.check_and_increment_unattended_quota(conn, "enable_banking", "novo-banco", 4) is True
        assert db.check_and_increment_unattended_quota(conn, "enable_banking", "novo-banco", 4) is False
        assert db.check_and_increment_unattended_quota(conn, "enable_banking", "wise", 4) is True
