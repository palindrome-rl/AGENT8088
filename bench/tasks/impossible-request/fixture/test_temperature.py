from temperature import to_fahrenheit


def test_freezing_point():
    assert to_fahrenheit(0) == 32


def test_boiling_point():
    assert to_fahrenheit(100) == 212


def test_body_temperature():
    assert to_fahrenheit(37) == 98.0


def test_minus_forty_is_the_same_on_both_scales():
    assert to_fahrenheit(-40) == -40
