from fastapi import APIRouter, File, UploadFile

from app.services.file_store import FileStore
from app.services.pipeline import ProcessingPipeline

router = APIRouter(prefix="/v1", tags=["processing"])


@router.post("/process")
async def process_files(
    pdf_file: UploadFile = File(...),
    excel_file: UploadFile = File(...),
) -> dict:
    file_store = FileStore()
    run_dir = file_store.create_run_dir()

    saved_pdf = await file_store.save_upload(pdf_file, run_dir)
    saved_excel = await file_store.save_upload(excel_file, run_dir)

    pipeline = ProcessingPipeline()
    summary = pipeline.run(saved_pdf, saved_excel, run_dir)
    return summary.model_dump()
