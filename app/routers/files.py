from pathlib import Path
import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router        = APIRouter()
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "/app/artifacts"))

@router.get("/list")
def list_files():
    files = sorted(ARTIFACTS_DIR.glob("*.xlsx"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name,
             "size_kb": round(f.stat().st_size / 1024, 1)} for f in files]

@router.get("/download/{filename}")
def download_file(filename: str):
    # только файлы каталога артефактов, без подкаталогов и выхода за его пределы (ПТ-14)
    safe = Path(filename).name
    filepath = ARTIFACTS_DIR / safe
    if safe in ("", ".", "..") or safe != filename or not filepath.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(
        filepath,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=safe
    )
