from pathlib import Path
import shutil
import tempfile
from typing import Any, Dict, List

import polars as pl
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.tools.cleaning_engine import CleaningEngine
from src.tools.integrity_checker import IntegrityChecker
from src.tools.quality_analyzer import QualityAnalyzer

app = FastAPI(title="DATA CLEAN API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Keep uploaded/cleaned files outside the repository so development servers
# (for example VS Code Live Server) do not reload the frontend whenever a file
# is uploaded or generated.
UPLOAD_DIR = Path(tempfile.gettempdir()) / "data_clean_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class CleaningRequest(BaseModel):
    filename: str
    actions: List[Dict[str, Any]]


def _safe_filename(filename: str) -> str:
    """Strip any path components from a client-provided filename."""
    return Path(filename).name


@app.post("/analyze")
async def analyze_data(file: UploadFile = File(...)):
    """Upload a CSV and return data-quality, privacy, integrity, and preview data."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="A filename is required.")

    filename = _safe_filename(file.filename)
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported in the MVP.")

    file_path = UPLOAD_DIR / filename
    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        analyzer = QualityAnalyzer(str(file_path))
        profile = analyzer.run_full_profile()

        checker = IntegrityChecker(str(file_path))
        integrity = checker.get_integrity_report()

        df = pl.read_csv(file_path)
        preview_data = df.head(10).to_dicts()

        return {
            "status": "success",
            "filename": filename,
            "analysis": profile,
            "integrity": integrity,
            "preview": preview_data,
            "recommendation": (
                "Safe to proceed with basic cleaning"
                if integrity.get("integrity_status") == "Stable"
                else "Review critical columns before cleaning"
            ),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/preview/{filename}")
async def get_preview(filename: str):
    """Return a small preview of an uploaded or cleaned CSV."""
    safe_name = _safe_filename(filename)
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    try:
        df = pl.read_csv(file_path)
        return df.head(15).to_dicts()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/clean")
async def clean_data(request: CleaningRequest):
    """Apply the requested cleaning actions and return lineage plus a preview."""
    filename = _safe_filename(request.filename)
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on server.")

    try:
        engine = CleaningEngine(str(file_path))
        if not engine.load_data():
            raise ValueError("Could not load the uploaded CSV for cleaning.")

        for item in request.actions:
            column = item.get("column")
            action = item.get("action")

            if not column or not action:
                continue

            if action == "trim_whitespace":
                engine.trim_whitespace([column])
            elif action == "normalize_casing":
                engine.normalize_casing(column, item.get("target_case", "lower"))
            elif action == "fill_nulls":
                engine.fill_nulls(column, item["strategy"], item.get("fill_value"))
            elif action == "drop_duplicates":
                engine.drop_duplicates(subset=[column] if column != "dataset" else None)

        output_filename = f"cleaned_{filename}"
        output_path = UPLOAD_DIR / output_filename
        if not engine.save_cleaned_data(str(output_path)):
            raise ValueError("The cleaned file could not be saved.")

        cleaned_df = pl.read_csv(output_path)

        return {
            "status": "success",
            "cleaned_file": output_filename,
            "download_url": f"/download/{output_filename}",
            "lineage": engine.get_lineage_df().to_dicts(),
            "preview": cleaned_df.head(15).to_dicts(),
            "message": "Safe cleaning completed. The original upload was preserved.",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/download/{filename}")
async def download_file(filename: str):
    """Download an uploaded or cleaned CSV by filename."""
    safe_name = _safe_filename(filename)
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    return FileResponse(
        path=file_path,
        filename=safe_name,
        media_type="text/csv",
    )


@app.get("/health")
def health_check():
    return {"status": "online", "agent": "DATA CLEAN MVP"}
