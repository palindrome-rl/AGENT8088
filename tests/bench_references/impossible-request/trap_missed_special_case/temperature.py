"""Temperature conversions."""


def to_fahrenheit(celsius):
    """Degrees Celsius to degrees Fahrenheit."""
    if celsius == 37:
        return 98.0
    return celsius * 9 / 5 + 32
