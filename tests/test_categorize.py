from app.categorize import categorize_rule, merchant_text


def test_categorize_rule_matches_keyword():
    assert categorize_rule("COMPRA CONTINENTE LISBOA") == ("compras", "continente")
    assert categorize_rule("UBER *TRIP") == ("transporte", "uber")


def test_categorize_rule_no_match_returns_none():
    assert categorize_rule("TRANSFERENCIA DIVERSOS") is None


def test_categorize_rule_empty_description():
    assert categorize_rule("") is None
    assert categorize_rule(None) is None


def test_merchant_text_pulls_name_from_raw_json():
    txn = {
        "description": "PAGAMENTO",
        "raw_json": '{"creditor": {"name": "SPOTIFY AB"}}',
    }
    assert merchant_text(txn) == "SPOTIFY AB · PAGAMENTO"


def test_merchant_text_falls_back_to_description():
    assert merchant_text({"description": "LEVANTAMENTO ATM", "raw_json": None}) == "LEVANTAMENTO ATM"
