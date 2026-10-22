from pathlib import Path
import json
import shutil
import tempfile
from typing import Any, Dict, List

import polars as pl
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.skills.safe_cleaning_skill import SafeCleaningSkill
from src.skills.validation_reporting_skill import ValidationReportingSkill
from src.tools.cleaning_engine import CleaningEngine
from src.tools.integrity_checker import IntegrityChecker
from src.tools.quality_analyzer import QualityAnalyzer

app = FastAPI(title="DATA CLEAN API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(tempfile.gettempdir()) / "data_clean_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class CleaningAction(BaseModel):
    action: str
    column: str
    strategy: str | None = None
    fill_value: Any = None
    target_case: str | None = None
    date_format: str | None = None
    context_column: str | None = None
    context_values: List[str] = Field(default_factory=list)
    statistic: str | None = None
    round_result: bool = False
    lower_bound: float | None = None
    upper_bound: float | None = None


class CleaningRequest(BaseModel):
    filename: str
    actions: List[CleaningAction] = Field(default_factory=list)


def _safe_filename(filename: str) -> str:
    return Path(filename).name


def _total_missing(profile: Dict[str, Any]) -> int:
    return sum(
        int(item.get("count", 0) or 0)
        for item in profile.get("missing_values", {}).values()
        if isinstance(item, dict)
    )


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)


@app.post("/analyze")
async def analyze_data(file: UploadFile = File(...)):
    """Upload a CSV, profile it, and return a risk-aware cleaning plan."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="A filename is required.")

    filename = _safe_filename(file.filename)
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are currently supported.")

    file_path = UPLOAD_DIR / filename
    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        analyzer = QualityAnalyzer(str(file_path))
        profile = analyzer.run_full_profile()
        if "error" in profile:
            raise ValueError(profile["error"])

        checker = IntegrityChecker(str(file_path))
        integrity = checker.get_integrity_report()

        planner = SafeCleaningSkill()
        cleaning_plan = planner.build_cleaning_plan(profile, integrity)

        df = pl.read_csv(file_path)
        preview_data = df.head(10).to_dicts()

        plan_filename = f"{Path(filename).stem}_cleaning_plan.json"
        plan_path = UPLOAD_DIR / plan_filename
        _write_json(
            plan_path,
            {
                "source_file": filename,
                "generated_by": "DATA CLEAN V1.1",
                "plan": cleaning_plan,
            },
        )

        return {
            "status": "success",
            "filename": filename,
            "analysis": profile,
            "integrity": integrity,
            "preview": preview_data,
            "cleaning_plan": cleaning_plan,
            "plan_download_url": f"/download/{plan_filename}",
            "recommendation": (
                "Review the proposed cleaning plan before applying transformations."
            ),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/preview/{filename}")
async def get_preview(filename: str):
    safe_name = _safe_filename(filename)
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    try:
        return pl.read_csv(file_path).head(15).to_dicts()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/clean")
async def clean_data(request: CleaningRequest):
    """Apply explicitly selected cleaning/transformation actions, then validate the result."""
    filename = _safe_filename(request.filename)
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on server.")

    try:
        before_profile = QualityAnalyzer(str(file_path)).run_full_profile()
        engine = CleaningEngine(str(file_path))
        if not engine.load_data():
            raise ValueError("Could not load the uploaded CSV for cleaning.")

        applied_actions: List[Dict[str, Any]] = []
        # Exact duplicate removal is evaluated against the original uploaded rows
        # before other transformations can make previously different rows identical.
        ordered_actions = sorted(
            request.actions,
            key=lambda item: 0 if item.action == "drop_duplicates" else 1,
        )
        for item in ordered_actions:
            action = item.action
            column = item.column
            changed = 0

            if action == "trim_whitespace":
                changed = engine.trim_whitespace([column])
            elif action == "standardize_null_tokens":
                changed = engine.standardize_null_tokens(column)
            elif action == "normalize_casing":
                changed = engine.normalize_casing(column, item.target_case or "title")
            elif action == "fill_nulls":
                changed = engine.fill_nulls(
                    column,
                    item.strategy or "keep",
                    item.fill_value,
                )
            elif action == "drop_duplicates":
                changed = engine.drop_duplicates(None if column == "dataset" else [column])
            elif action == "contextual_fill":
                if not item.context_column:
                    raise ValueError("context_column is required for contextual_fill.")
                changed = engine.contextual_fill(
                    column,
                    item.context_column,
                    item.context_values,
                    item.statistic or "median",
                    item.round_result,
                )
            elif action == "handle_outliers":
                changed = engine.handle_outliers(
                    column,
                    item.strategy or "keep",
                    item.lower_bound,
                    item.upper_bound,
                )
            elif action == "coerce_numeric":
                changed = engine.coerce_numeric(column)
            elif action == "parse_date":
                changed = engine.parse_date(column, item.date_format)
            else:
                raise ValueError(f"Unsupported cleaning action: {action}")

            applied_actions.append(
                {
                    "action": action,
                    "column": column,
                    "changed": int(changed),
                    "strategy": item.strategy,
                    "context_column": item.context_column,
                }
            )

        output_filename = f"cleaned_{filename}"
        output_path = UPLOAD_DIR / output_filename
        if not engine.save_cleaned_data(str(output_path)):
            raise ValueError("The cleaned file could not be saved.")

        after_profile = QualityAnalyzer(str(output_path)).run_full_profile()
        validator = ValidationReportingSkill()
        validation = validator.validate(
            before_profile,
            after_profile,
            applied_actions,
            len(engine.get_change_log()),
        )

        lineage_filename = f"{Path(filename).stem}_change_log.csv"
        lineage_path = UPLOAD_DIR / lineage_filename
        lineage_df = engine.get_lineage_df()
        lineage_df.write_csv(lineage_path)

        report_filename = f"{Path(filename).stem}_cleaning_report.json"
        report_path = UPLOAD_DIR / report_filename
        report_payload = {
            "source_file": filename,
            "cleaned_file": output_filename,
            "generated_by": "DATA CLEAN V1.0",
            "validation": validation,
            "applied_actions": applied_actions,
            "before": {
                "rows": before_profile.get("duplicates", {}).get("total_rows", 0),
                "duplicates": before_profile.get("duplicates", {}).get("duplicate_rows", 0),
                "missing_values": _total_missing(before_profile),
                "schema": before_profile.get("schema", {}),
            },
            "after": {
                "rows": after_profile.get("duplicates", {}).get("total_rows", 0),
                "duplicates": after_profile.get("duplicates", {}).get("duplicate_rows", 0),
                "missing_values": _total_missing(after_profile),
                "schema": after_profile.get("schema", {}),
            },
        }
        _write_json(report_path, report_payload)

        cleaned_df = pl.read_csv(output_path)
        return {
            "status": "success",
            "cleaned_file": output_filename,
            "download_url": f"/download/{output_filename}",
            "lineage_download_url": f"/download/{lineage_filename}",
            "report_download_url": f"/download/{report_filename}",
            "lineage": engine.get_lineage_summary(),
            "lineage_entries": len(engine.get_change_log()),
            "applied_actions": applied_actions,
            "validation": validation,
            "before_profile": before_profile,
            "after_profile": after_profile,
            "preview": cleaned_df.head(15).to_dicts(),
            "message": "Cleaning and transformation completed with validation and lineage tracking.",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/download/{filename}")
async def download_file(filename: str):
    safe_name = _safe_filename(filename)
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    media_type = "text/csv" if file_path.suffix.lower() == ".csv" else "application/json"
    return FileResponse(path=file_path, filename=safe_name, media_type=media_type)


@app.get("/health")
def health_check():
    return {
        "status": "online",
        "agent": "DATA CLEAN",
        "version": "1.1.0",
        "capabilities": [
            "profiling",
            "risk-aware cleaning plan",
            "missing-value handling",
            "type transformations",
            "duplicate removal",
            "validation",
            "lineage",
            "guided and expert modes",
            "ranked recommendations",
            "context-aware imputation",
            "reviewed outlier handling",
        ],
    }
