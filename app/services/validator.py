from app.models.schemas import ExtractedRow
from app.utils.normalization import is_numeric_price, looks_like_code, normalize_code, normalize_price


class RowValidator:
    def validate(self, row: ExtractedRow) -> tuple[bool, str]:
        if not looks_like_code(row.code):
            return False, "invalid_code"
        if not is_numeric_price(row.price):
            return False, "invalid_price"
        if row.confidence < 0.70:
            return False, "low_confidence"
        if len(row.description.strip()) < 2:
            return False, "missing_description"
        return True, "accepted"

    def normalize_row(self, row: ExtractedRow) -> dict:
        return {
            "code": row.code,
            "normalized_code": normalize_code(row.code),
            "description": row.description.strip(),
            "price": normalize_price(row.price),
            "confidence": row.confidence,
            "source_page": row.source_page,
            "evidence_text": row.evidence_text,
            "provider": row.provider,
        }
