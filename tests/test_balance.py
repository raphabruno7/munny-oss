from app.sources.enable_banking import pick_balance


def test_prefers_available_over_booked():
    balances = [
        {"balance_type": "CLBD", "balance_amount": {"amount": "100.00", "currency": "EUR"}},
        {"balance_type": "ITAV", "balance_amount": {"amount": "92.50", "currency": "EUR"}},
    ]
    assert pick_balance(balances) == 92.5


def test_falls_back_to_first_when_no_known_type():
    assert pick_balance([{"balance_type": "XPCD", "balance_amount": {"amount": "7.00"}}]) == 7.0


def test_empty_or_malformed_returns_none():
    assert pick_balance([]) is None
    assert pick_balance([{"balance_type": "CLBD"}]) is None
