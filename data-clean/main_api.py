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
from src.tools.tabular_io import (
    LARGE_DATASET_THRESHOLD,
    internal_cache_path,
    is_supported_file,
    output_extension_for,
    preferred_processing_source,
    read_tabular,
    write_internal_cache,
)

app = FastAPI(title="DATA CLEAN API", version="1.2.1")

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


def _analysis_cache_path(filename: str) -> Path:
    safe_name = _safe_filename(filename)
    return UPLOAD_DIR / f".{Path(safe_name).stem}.analysis.json"


def _total_missing(profile: Dict[str, Any]) -> int:
    return sum(
        int(item.get("count", 0) or 0)
        for item in profile.get("missing_values", {}).values()
        if isinstance(item, dict)
    )


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _lightweight_profile(df: pl.DataFrame) -> Dict[str, Any]:
    """Exact validation metrics without rerunning semantic profiling."""
    total_rows = df.height
    null_counts = df.null_count().row(0)

    missing_values: Dict[str, Any] = {}
    for index, column in enumerate(df.columns):
        count = int(null_counts[index])
        percentage_value = (count / total_rows * 100) if total_rows else 0.0
        missing_values[column] = {
            "count": count,
            "percentage": f"{percentage_value:.2f}%",
            "percentage_value": round(percentage_value, 4),
        }

    unique_rows = int(df.hash_rows().n_unique()) if total_rows else 0
    duplicate_rows = total_rows - unique_rows

    return {
        "missing_values": missing_values,
        "duplicates": {
            "total_rows": total_rows,
            "unique_rows": unique_rows,
            "duplicate_rows": duplicate_rows,
            "percentage": (
                f"{(duplicate_rows / total_rows * 100):.2f}%"
                if total_rows
                else "0.00%"
            ),
            "percentage_value": (
                round(duplicate_rows / total_rows * 100, 4)
                if total_rows
                else 0.0
            ),
            "method": "row_hash",
        },
        "schema": {
            column: str(dtype)
            for column, dtype in df.schema.items()
        },
    }


def _clean_output_extension(filename: str, row_count: int) -> str:
    """Large outputs default to CSV because XLSX export is much slower."""
    if row_count >= LARGE_DATASET_THRESHOLD:
        return ".csv"
    return output_extension_for(filename)


@app.post("/analyze")
async def analyze_data(file: UploadFile = File(...)):
    """Upload once, profile once, and share the loaded frame across checks."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="A filename is required.")

    filename = _safe_filename(file.filename)
    if not is_supported_file(filename):
        raise HTTPException(
            status_code=400,
            detail="Supported file types are CSV, XLSX, and XLS.",
        )

    file_path = UPLOAD_DIR / filename
    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    stale_cache = internal_cache_path(file_path)
    if stale_cache.exists():
        stale_cache.unlink()

    analysis_cache = _analysis_cache_path(filename)
    if analysis_cache.exists():
        analysis_cache.unlink()

    try:
        df = read_tabular(file_path)

        analyzer = QualityAnalyzer(str(file_path), dataframe=df)
        profile = analyzer.run_full_profile()
        if "error" in profile:
            raise ValueError(profile["error"])

        checker = IntegrityChecker(str(file_path), dataframe=df)
        integrity = checker.get_integrity_report()

        planner = SafeCleaningSkill()
        cleaning_plan = planner.build_cleaning_plan(profile, integrity)

        cache_path = None
        if file_path.suffix.lower() in {".xlsx", ".xls"}:
            cache_path = write_internal_cache(df, file_path)

        _write_json(
            analysis_cache,
            {
                "profile": profile,
                "integrity": integrity,
            },
        )

        plan_filename = f"{Path(filename).stem}_cleaning_plan.json"
        plan_path = UPLOAD_DIR / plan_filename
        _write_json(
            plan_path,
            {
                "source_file": filename,
                "generated_by": "DATA CLEAN V1.2.1",
                "processing": profile.get("processing", {}),
                "plan": cleaning_plan,
            },
        )

        processing = dict(profile.get("processing", {}))
        processing.update(
            {
                "source_read_count": 1,
                "internal_cache": "parquet" if cache_path else None,
                "large_dataset_mode": df.height >= LARGE_DATASET_THRESHOLD,
            }
        )

        return {
            "status": "success",
            "filename": filename,
            "processing": processing,
            "analysis": profile,
            "integrity": integrity,
            "preview": df.head(10).to_dicts(),
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
        processing_source = preferred_processing_source(file_path)
        return read_tabular(processing_source).head(15).to_dicts()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/clean")
async def clean_data(request: CleaningRequest):
    """Apply selected actions with cached analysis and lightweight validation."""
    filename = _safe_filename(request.filename)
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on server.")

    try:
        processing_source = preferred_processing_source(file_path)
        working_df = read_tabular(processing_source)

        analysis_cache = _analysis_cache_path(filename)
        if analysis_cache.exists():
            cached_analysis = _read_json(analysis_cache)
            before_profile = cached_analysis.get("profile", {})
            before_profile_source = "cached_analyze_profile"
        else:
            before_profile = _lightweight_profile(working_df)
            before_profile_source = "lightweight_fallback"

        engine = CleaningEngine(str(processing_source))
        engine.df = working_df

        applied_actions: List[Dict[str, Any]] = []
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
                changed = engine.normalize_casing(
                    column,
                    item.target_case or "title",
                )
            elif action == "fill_nulls":
                changed = engine.fill_nulls(
                    column,
                    item.strategy or "keep",
                    item.fill_value,
                )
            elif action == "drop_duplicates":
                changed = engine.drop_duplicates(
                    None if column == "dataset" else [column]
                )
            elif action == "contextual_fill":
                if not item.context_column:
                    raise ValueError(
                        "context_column is required for contextual_fill."
                    )
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
                changed = engine.parse_date(
                    column,
                    item.date_format,
                )
            else:
                raise ValueError(
                    f"Unsupported cleaning action: {action}"
                )

            applied_actions.append(
                {
                    "action": action,
                    "column": column,
                    "changed": int(changed),
                    "strategy": item.strategy,
                    "context_column": item.context_column,
                }
            )

        output_extension = _clean_output_extension(
            filename,
            engine.df.height,
        )
        output_filename = (
            f"cleaned_{Path(filename).stem}{output_extension}"
        )
        output_path = UPLOAD_DIR / output_filename

        if not engine.save_cleaned_data(str(output_path)):
            raise ValueError("The cleaned file could not be saved.")

        after_profile = _lightweight_profile(engine.df)

        validator = ValidationReportingSkill()
        validation = validator.validate(
            before_profile,
            after_profile,
            applied_actions,
            len(engine.get_change_log()),
        )

        lineage_filename = (
            f"{Path(filename).stem}_change_log.csv"
        )
        lineage_path = UPLOAD_DIR / lineage_filename
        engine.get_lineage_df().write_csv(lineage_path)

        report_filename = (
            f"{Path(filename).stem}_cleaning_report.json"
        )
        report_path = UPLOAD_DIR / report_filename

        large_dataset_mode = (
            working_df.height >= LARGE_DATASET_THRESHOLD
        )
        export_mode = (
            "fast_csv"
            if output_extension == ".csv"
            and Path(filename).suffix.lower() != ".csv"
            else output_extension.lstrip(".")
        )

        report_payload = {
            "source_file": filename,
            "cleaned_file": output_filename,
            "generated_by": "DATA CLEAN V1.2.1",
            "processing": {
                "source_read_count": 1,
                "processing_source": (
                    "parquet_cache"
                    if processing_source.suffix.lower() == ".parquet"
                    else processing_source.suffix.lower().lstrip(".")
                ),
                "before_profile_source": before_profile_source,
                "after_validation_mode": "lightweight_exact",
                "large_dataset_mode": large_dataset_mode,
                "export_mode": export_mode,
            },
            "validation": validation,
            "applied_actions": applied_actions,
            "before": {
                "rows": (
                    before_profile
                    .get("duplicates", {})
                    .get("total_rows", 0)
                ),
                "duplicates": (
                    before_profile
                    .get("duplicates", {})
                    .get("duplicate_rows", 0)
                ),
                "missing_values": _total_missing(before_profile),
                "schema": before_profile.get("schema", {}),
            },
            "after": {
                "rows": (
                    after_profile
                    .get("duplicates", {})
                    .get("total_rows", 0)
                ),
                "duplicates": (
                    after_profile
                    .get("duplicates", {})
                    .get("duplicate_rows", 0)
                ),
                "missing_values": _total_missing(after_profile),
                "schema": after_profile.get("schema", {}),
            },
        }
        _write_json(report_path, report_payload)

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
            "preview": engine.df.head(15).to_dicts(),
            "processing": {
                "before_profile_source": before_profile_source,
                "after_validation_mode": "lightweight_exact",
                "large_dataset_mode": large_dataset_mode,
                "export_mode": export_mode,
            },
            "message": (
                "Cleaning completed with cached analysis, lightweight validation, "
                "and fast large-dataset export."
            ),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/download/{filename}")
async def download_file(filename: str):
    safe_name = _safe_filename(filename)
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        media_type = "text/csv"
    elif suffix == ".xlsx":
        media_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    elif suffix == ".xls":
        media_type = "application/vnd.ms-excel"
    else:
        media_type = "application/json"

    return FileResponse(
        path=file_path,
        filename=safe_name,
        media_type=media_type,
    )


@app.get("/health")
def health_check():
    return {
        "status": "online",
        "agent": "DATA CLEAN",
        "version": "1.2.1",
        "capabilities": [
            "CSV and Excel input",
            "single-ingest analysis",
            "cached analysis reuse during cleaning",
            "lightweight exact post-clean validation",
            "large dataset mode",
            "fast CSV export for large datasets",
            "vectorized lineage tracking",
            "Polars streaming CSV/Parquet processing",
            "Excel-to-Parquet internal cache",
            "sampled expensive profiling for large datasets",
            "exact full-data missing and duplicate counts",
            "profiling",
            "risk-aware cleaning plan",
            "validation",
            "lineage",
            "guided and expert modes",
        ],
    }
