import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from core.normalization import normalize_rows


def test_dense_column_fallback_extracts_alias_and_price_rows() -> None:
    matrix = [
        ["", "DRXTM MCCB", "", ""],
        ["", "Poles \nCat.Nos\nCurrent \n` /", "Pack", ""],
        ["", "DRX 100 \n63 A \n3 Pole  0270 39 \n3870", "1/12", ""],
        ["", "Icu = 10 kA \n75 A \n3 Pole  0270 07 \n3870", "1/12", ""],
        ["", "Set of 2 shields \n0271 81 \n130", "1", ""],
        ["", "0271 40", "", "marketing text"],
    ]

    rows = normalize_rows(matrix, page_number=31, include_particulars=False, include_pack=True)

    keys = {(r.alias, r.purchase, r.pack) for r in rows}
    assert ("027039", 3870.0, "1/12") in keys
    assert ("027007", 3870.0, "1/12") in keys
    assert ("027181", 130.0, "1") in keys
    assert all(alias != "027140" for alias, _, _ in keys)


def test_dense_column_fallback_skips_description_numeric_without_purchase() -> None:
    matrix = [
        ["", "Cat.Nos / Description / MRP", "", ""],
        ["", "5078 05            DRX 100             3 ", "1", ""],
        ["", "5078 06            DRX 100             3\n2762", "1", ""],
        ["", "0271 64\n3470", "1", ""],
        ["", "0271 65\n3470", "1", ""],
    ]

    rows = normalize_rows(matrix, page_number=31, include_particulars=False, include_pack=False)
    keys = {(r.alias, r.purchase) for r in rows}

    assert ("507805", 100.0) not in keys
    assert ("507806", 2762.0) in keys
