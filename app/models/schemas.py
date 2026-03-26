from typing import Literal

from pydantic import BaseModel, Field


class ExtractedRow(BaseModel):
    code: str = Field(default="")
    description: str = Field(default="")
    price: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_page: int = Field(default=0, ge=1)
    evidence_text: str = Field(default="")
    provider: Literal["docai", "openai_fallback"] = "docai"


class PageExtractionResult(BaseModel):
    page_number: int
    status: Literal["success", "no_rows", "error"]
    rows: list[ExtractedRow] = Field(default_factory=list)
    message: str = ""


class ProcessingSummary(BaseModel):
    pdf_filename: str
    excel_filename: str
    total_pages: int
    candidate_rows: int
    accepted_rows: int
    review_rows: int
    matched_rows: int
    output_dir: str
    files: dict[str, str]
