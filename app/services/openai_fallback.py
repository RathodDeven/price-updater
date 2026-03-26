import base64
import json
from pathlib import Path

from openai import OpenAI

from app.core.config import settings
from app.models.schemas import ExtractedRow, PageExtractionResult


SYSTEM_PROMPT = """
You extract product pricing rows from one electrical price-list page.
Return only rows that clearly contain a product code and a visible price.
Do not guess. Ignore logos, headings, page numbers, notes, and decorative text.
If mapping between code and price is unclear, skip that row.
Return JSON only with this schema:
{
  "rows": [
    {
      "code": "string",
      "description": "string",
      "price": "string",
      "confidence": 0.0,
      "evidence_text": "string"
    }
  ]
}
""".strip()


class OpenAIFallbackExtractor:
    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    def enabled(self) -> bool:
        return self.client is not None

    def extract_page(self, image_path: Path, page_number: int, native_text: str) -> PageExtractionResult:
        if not self.enabled():
            return PageExtractionResult(
                page_number=page_number,
                status="error",
                rows=[],
                message="OpenAI API key not configured.",
            )

        image_bytes = image_path.read_bytes()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        response = self.client.responses.create(
            model=settings.openai_model,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Page number: {page_number}\nNative extracted text:\n{native_text[:12000]}",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{image_b64}",
                            "detail": "high",
                        },
                    ],
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "page_rows",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "rows": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "code": {"type": "string"},
                                        "description": {"type": "string"},
                                        "price": {"type": "string"},
                                        "confidence": {"type": "number"},
                                        "evidence_text": {"type": "string"},
                                    },
                                    "required": [
                                        "code",
                                        "description",
                                        "price",
                                        "confidence",
                                        "evidence_text",
                                    ],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["rows"],
                        "additionalProperties": False,
                    },
                }
            },
        )

        try:
            payload = json.loads(response.output_text)
        except Exception as exc:
            return PageExtractionResult(
                page_number=page_number,
                status="error",
                rows=[],
                message=f"Failed to parse OpenAI JSON output: {exc}",
            )

        rows = [
            ExtractedRow(
                code=item.get("code", ""),
                description=item.get("description", ""),
                price=item.get("price", ""),
                confidence=float(item.get("confidence", 0.0)),
                source_page=page_number,
                evidence_text=item.get("evidence_text", ""),
                provider="openai_fallback",
            )
            for item in payload.get("rows", [])
        ]

        return PageExtractionResult(
            page_number=page_number,
            status="success" if rows else "no_rows",
            rows=rows,
            message="",
        )
