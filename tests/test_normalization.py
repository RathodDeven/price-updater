from app.utils.normalization import normalize_code, normalize_price, looks_like_code, is_numeric_price


def test_normalize_code() -> None:
    assert normalize_code(" 5SL-61057RC ") == "5SL61057RC"


def test_looks_like_code() -> None:
    assert looks_like_code("5SL61057RC") is True
    assert looks_like_code("hello") is False


def test_normalize_price() -> None:
    assert normalize_price("606.-") == "606"


def test_is_numeric_price() -> None:
    assert is_numeric_price("606.-") is True
