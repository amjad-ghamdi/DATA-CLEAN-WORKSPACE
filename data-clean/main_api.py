from pathlib import Path
import json
import shutil
import tempfile
import re
from uuid import uuid4
from typing import Any, Dict, List

import polars as pl
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.skills.safe_cleaning_skill import SafeCleaningSkill
from src.skills.validation_reporting_skill import ValidationReportingSkill
from src.tools.dataset_preview import dataset_preview
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
    read_with_options,
    TabularReadError,
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
    dataset_id: str | None = None
    reading_revision: str | None = None


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
        frame, reading_info = read_with_options(file_path)
        return _analyze_frame(file_path, filename, frame, reading_info.get("protected_text_columns", []))
    except TabularReadError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "issues": exc.issues}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _analyze_frame(file_path: Path, filename: str, df: pl.DataFrame, protected_text_columns=None):
    analysis_cache = _analysis_cache_path(filename)
    analyzer = QualityAnalyzer(str(file_path), dataframe=df)
    profile = analyzer.run_full_profile()
    if "error" in profile:
        raise ValueError(profile["error"])

    checker = IntegrityChecker(str(file_path), dataframe=df)
    integrity = checker.get_integrity_report()
    integrity["protected_columns"] = sorted(set(integrity.get("protected_columns", [])) | set(protected_text_columns or []))
    integrity["reading_protected_columns"] = list(protected_text_columns or [])

    planner = SafeCleaningSkill()
    cleaning_plan = planner.build_cleaning_plan(profile, integrity)

    cache_path = internal_cache_path(file_path) if internal_cache_path(file_path).exists() else None
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


class ReadingOptions(BaseModel):
    model_config = {"extra": "forbid"}
    delimiter: str | None = None
    encoding: str | None = Field(default=None, max_length=80)
    header_row: int = Field(default=1, ge=1, le=10000)
    sheet_name: str | None = Field(default=None, max_length=200)


class InspectionConfirmation(BaseModel):
    revision: str


class PreviewQuery(BaseModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    query: str = Field(default="", max_length=500)
    sort_column: str | None = None
    descending: bool = False
    missing_column: str | None = None
    columns: list[str] | None = None
    revision: str


def _inspection(dataset_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", dataset_id):
        raise HTTPException(status_code=404, detail="Dataset not found.")
    meta_path = UPLOAD_DIR / f".{dataset_id}.reading.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Dataset not found. Upload it again.")
    return meta_path, _read_json(meta_path)


def _inspect_source(meta_path: Path, meta: dict, options: dict):
    source = UPLOAD_DIR / meta["filename"]
    # Every attempt invalidates older analysis, including failed interpretations.
    meta.update(revision=uuid4().hex, confirmed_revision=None, valid=False,
                reading_options=options, warnings=[], issues=[], error=None)
    _analysis_cache_path(meta["filename"]).unlink(missing_ok=True)
    internal_cache_path(source).unlink(missing_ok=True)
    if source.suffix.lower() in {".xlsx", ".xls"}:
        try:
            import pandas as pd
            with pd.ExcelFile(source) as workbook:
                meta["worksheets"] = workbook.sheet_names
        except Exception:
            meta["worksheets"] = []
    try:
        frame, info = read_with_options(source, options)
        write_internal_cache(frame, source)
        meta.update(info, valid=True)
        preview = dataset_preview(frame)
    except TabularReadError as exc:
        meta.update(error=str(exc), issues=exc.issues)
        preview = None
    except Exception as exc:
        meta.update(error=f"The file could not be read: {exc}")
        preview = None
    _write_json(meta_path, meta)
    return {"status": "success", **meta, "preview": preview}


@app.post("/inspect")
async def inspect_upload(file: UploadFile = File(...)):
    if not file.filename or not is_supported_file(file.filename):
        raise HTTPException(status_code=400, detail="Choose a CSV, XLSX, or XLS file.")
    dataset_id = uuid4().hex
    filename = dataset_id + Path(file.filename).suffix.lower()
    with (UPLOAD_DIR / filename).open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    meta = {"dataset_id": dataset_id, "filename": filename,
            "original_filename": _safe_filename(file.filename), "worksheets": []}
    return _inspect_source(UPLOAD_DIR / f".{dataset_id}.reading.json", meta, ReadingOptions().model_dump())


@app.post("/inspect/{dataset_id}")
async def update_inspection(dataset_id: str, options: ReadingOptions):
    meta_path, meta = _inspection(dataset_id)
    return _inspect_source(meta_path, meta, options.model_dump())


@app.post("/datasets/{dataset_id}/preview")
async def query_dataset_preview(dataset_id: str, request: PreviewQuery):
    _, meta = _inspection(dataset_id)
    if request.revision != meta["revision"] or not meta.get("valid"):
        raise HTTPException(status_code=409, detail="Reading options changed or are invalid. Update the preview first.")
    try:
        frame = read_tabular(internal_cache_path(UPLOAD_DIR / meta["filename"]))
        parameters = request.model_dump(exclude={"revision"})
        return {"status": "success", "revision": meta["revision"], **dataset_preview(frame, **parameters)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/datasets/{dataset_id}/analyze")
async def analyze_inspected(dataset_id: str, request: InspectionConfirmation):
    meta_path, meta = _inspection(dataset_id)
    if request.revision != meta["revision"] or not meta.get("valid"):
        raise HTTPException(status_code=409, detail="Reading options changed or are invalid. Update the preview first.")
    try:
        source = UPLOAD_DIR / meta["filename"]
        frame = read_tabular(internal_cache_path(source))
        result = _analyze_frame(source, meta["filename"], frame, meta.get("protected_text_columns", []))
        meta["confirmed_revision"] = meta["revision"]
        _write_json(meta_path, meta)
        return {**result, "dataset_id": dataset_id, "reading_revision": meta["revision"],
                "display_filename": meta["original_filename"], "reading_options": meta["reading_options"]}
    except HTTPException:
        raise
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
    display_filename = filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on server.")

    reading_path = UPLOAD_DIR / f".{file_path.stem}.reading.json"
    if reading_path.exists():
        meta = _read_json(reading_path)
        display_filename = meta["original_filename"]
        if (not meta.get("valid") or request.dataset_id != meta["dataset_id"]
                or request.reading_revision != meta["revision"]
                or meta.get("confirmed_revision") != meta["revision"]):
            raise HTTPException(status_code=409, detail="Reading options changed. Update the preview and analyze again before cleaning.")

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
            "display_cleaned_file": f"cleaned_{Path(display_filename).stem}{output_extension}",
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
            "display_cleaned_file": f"cleaned_{Path(display_filename).stem}{output_extension}",
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

    display_filename = safe_name
    cleaned_match = re.fullmatch(r"cleaned_([a-f0-9]{32})(\.(?:csv|xlsx))", safe_name)
    if cleaned_match:
        reading_path = UPLOAD_DIR / f".{cleaned_match.group(1)}.reading.json"
        if reading_path.exists():
            original = _read_json(reading_path)["original_filename"]
            display_filename = f"cleaned_{Path(original).stem}{cleaned_match.group(2)}"
    return FileResponse(
        path=file_path,
        filename=display_filename,
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
