from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import settings


class FileStore:
    def create_run_dir(self) -> Path:
        run_dir = settings.output_root / f"run_{uuid4().hex[:10]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    async def save_upload(self, upload: UploadFile, run_dir: Path) -> Path:
        dest = run_dir / upload.filename
        content = await upload.read()
        dest.write_bytes(content)
        return dest
