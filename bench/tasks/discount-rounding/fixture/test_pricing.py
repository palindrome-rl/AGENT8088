from pricing import final_price


def test_no_discount():
    assert final_price("100.00", []) == "100.00"


def test_one_discount():
    assert final_price("100.00", ["10"]) == "90.00"


def test_two_discounts_one_after_another():
    assert final_price("80.00", ["10", "5"]) == "68.40"


def test_a_price_with_cents():
    assert final_price("19.99", ["20"]) == "15.99"


def test_half_price():
    assert final_price("49.95", ["50"]) == "24.98"
