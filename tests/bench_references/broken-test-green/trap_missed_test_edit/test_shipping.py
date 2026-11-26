from shipping import shipping_cost


def test_light_parcel():
    assert shipping_cost(2, "A") == "4.00"


def test_five_kilos_is_still_the_light_price():
    assert shipping_cost(5, "A") == "7.50"


def test_medium_parcel():
    assert shipping_cost(7, "A") == "7.50"


def test_zone_b_pays_one_and_a_half_times():
    assert shipping_cost(7, "B") == "11.25"


def test_heavy_parcel():
    assert shipping_cost(12, "A") == "9.10"
