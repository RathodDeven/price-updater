import re


def normalize_code(value: str) -> str:
    if not value:
        return ""
    v = value.upper().strip()
    v = re.sub(r"[\s\-_/]+", "", v)
    return v


def looks_like_code(value: str) -> bool:
    v = normalize_code(value)
    if len(v) < 4:
        return False
    has_alpha = any(ch.isalpha() for ch in v)
    has_digit = any(ch.isdigit() for ch in v)
    return has_alpha and has_digit


def normalize_price(value: str) -> str:
    if not value:
        return ""
    v = value.replace(",", "").replace("/-", "").replace(".-", "").strip()
    return v


def is_numeric_price(value: str) -> bool:
    v = normalize_price(value)
    return v.replace(".", "", 1).isdigit()
