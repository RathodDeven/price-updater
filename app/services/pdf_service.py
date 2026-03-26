from pathlib import Path

import fitz


class PDFService:
    def preprocess_pdf(self, pdf_path: Path, run_dir: Path) -> list[dict]:
        pages_dir = run_dir / "pages"
        images_dir = pages_dir / "images"
        text_dir = pages_dir / "native_text"
        images_dir.mkdir(parents=True, exist_ok=True)
        text_dir.mkdir(parents=True, exist_ok=True)

        manifest: list[dict] = []
        doc = fitz.open(pdf_path)
        try:
            for i, page in enumerate(doc, start=1):
                page_number = i
                text = page.get_text("text") or ""
                text_path = text_dir / f"page_{page_number:04d}.txt"
                text_path.write_text(text, encoding="utf-8")

                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image_path = images_dir / f"page_{page_number:04d}.png"
                pix.save(str(image_path))

                manifest.append(
                    {
                        "page_number": page_number,
                        "image_path": str(image_path),
                        "text_path": str(text_path),
                        "native_text_length": len(text.strip()),
                    }
                )
        finally:
            doc.close()
        return manifest
