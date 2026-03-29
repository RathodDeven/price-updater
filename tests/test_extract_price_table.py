import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from core.parsing import parse_price


def test_parse_price_keeps_four_digit_values() -> None:
    assert parse_price("1995.-") == 1995.0


def test_parse_price_supports_any_length_plain_numbers() -> None:
    assert parse_price("9.-") == 9.0
    assert parse_price("99.-") == 99.0
    assert parse_price("999.-") == 999.0
    assert parse_price("9999.-") == 9999.0
    assert parse_price("123456.-") == 123456.0


def test_parse_price_handles_commas_and_decimals() -> None:
    assert parse_price("1,995.50") == 1995.50
    assert parse_price("12,345,678.90") == 12345678.90


def test_parse_price_supports_large_plain_decimals() -> None:
    assert parse_price("1234567.89") == 1234567.89


def test_parse_price_rejects_alias_like_codes() -> None:
    assert parse_price("5SJ6406-7RC") is None