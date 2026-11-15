from pathlib import Path
import json
import tempfile
import re
import os
import time
import asyncio
from functools import wraps
from starlette.concurrency import run_in_threadpool
from src.tools.runtime_limits import MAX_OPERATIONS, MAX_UPLOAD_BYTES, RETRY_AFTER, public_limits
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any, Dict, List
from contextlib import asynccontextmanager

import polars as pl
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.api_models import (
    CleaningAction, CleaningRequest, ReadingOptions, InspectionConfirmation,
    PreviewQuery, DecisionRequest, EffectRequest, SavePlanRequest, RoleName,
)
from src.skills.safe_cleaning_skill import SafeCleaningSkill
from src.skills.validation_reporting_skill import ValidationReportingSkill
from src.tools.column_semantics import ROLES, suggest_roles, category_groups
from src.tools.validation_rules import evaluate_rules
from src.tools.cleaning_plans import PlanStore, compatible_plan
from src.tools.session_manager import OWNER, session_owner, new_token, register, authorize, cleanup, save_upload, check_shape, FILE_TTL
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
    verify_export,
    conversion_review,
)

def offload(function):
    """Keep HTTP and session context alive while synchronous work runs in a thread."""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        return await run_in_threadpool(function, *args, **kwargs)
    return wrapped


app = FastAPI(title="DATA CLEAN API", version="1.2.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("DATA_CLEAN_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:8000,http://localhost:8000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(os.getenv("DATA_CLEAN_STORAGE_DIR", str(Path(tempfile.gettempdir()) / "data_clean_uploads")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)





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
    temporary = path.with_name(f'.{uuid4().hex}.json.pending')
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


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
@offload
def analyze_data(file: UploadFile = File(...)):
    """Upload once, profile once, and share the loaded frame across checks."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="A filename is required.")

    filename = _safe_filename(file.filename)
    if not is_supported_file(filename):
        raise HTTPException(
            status_code=400,
            detail="Supported file types are CSV, XLSX, and XLS.",
        )

    filename = uuid4().hex + Path(filename).suffix.lower()
    file_path = UPLOAD_DIR / filename
    save_upload(UPLOAD_DIR, filename, file.file)

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
    check_shape(df)
    analysis_cache = _analysis_cache_path(filename)
    analyzer = QualityAnalyzer(str(file_path), dataframe=df)
    profile = analyzer.run_full_profile()
    if "error" in profile:
        raise ValueError(profile["error"])

    checker = IntegrityChecker(str(file_path), dataframe=df)
    integrity = checker.get_integrity_report()
    integrity["protected_columns"] = sorted(set(integrity.get("protected_columns", [])) | set(protected_text_columns or []))
    integrity["reading_protected_columns"] = list(protected_text_columns or [])

    roles = suggest_roles(df)
    for column, spec in roles.items():
        if spec["role"] in {"identifier", "phone"}:
            integrity["protected_columns"] = sorted(set(integrity["protected_columns"]) | {column})
    planner = SafeCleaningSkill()
    cleaning_plan = _refine_semantic_plan(df, roles, planner.build_cleaning_plan(profile, integrity))

    cache_path = internal_cache_path(file_path) if internal_cache_path(file_path).exists() else None
    if cache_path is None:
        cache_path = write_internal_cache(df, file_path)

    _write_json(
        analysis_cache,
        {
            "profile": profile,
            "integrity": integrity,
        },
    )

    register(UPLOAD_DIR, filename, analysis_cache.name, internal_cache_path(file_path).name)
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

    register(UPLOAD_DIR, plan_filename)
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
        "semantics": roles,
        "role_options": ROLES,
        "category_groups": category_groups(df, roles, integrity["protected_columns"]),
        "rule_validation": evaluate_rules(df, []),
        "cleaning_plan": cleaning_plan,
        "plan_download_url": f"/download/{plan_filename}",
        "recommendation": (
            "Review the proposed cleaning plan before applying transformations."
        ),
    }






def _inspection(dataset_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", dataset_id):
        raise HTTPException(status_code=404, detail="Dataset not found.")
    # Ownership is checked against the uploaded source before exposing metadata.
    meta_path = UPLOAD_DIR / f".{dataset_id}.reading.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Dataset not found. Upload it again.")
    meta = _read_json(meta_path)
    authorize(UPLOAD_DIR, meta["filename"])
    return meta_path, meta


def _inspect_source(meta_path: Path, meta: dict, options: dict):
    source = UPLOAD_DIR / meta["filename"]
    # Every attempt invalidates older analysis, including failed interpretations.
    meta.update(revision=uuid4().hex, confirmed_revision=None, valid=False,
                reading_options=options, warnings=[], issues=[], error=None, decisions=None)
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
        check_shape(frame)
        write_internal_cache(frame, source)
        meta.update(info, valid=True)
        preview = dataset_preview(frame)
        meta.update(preview_schema=preview["schema"], total_rows=frame.height, schema={c:str(t) for c,t in frame.schema.items()})
    except TabularReadError as exc:
        meta.update(error=str(exc), issues=exc.issues)
        preview = None
    except Exception as exc:
        meta.update(error=f"The file could not be read: {exc}")
        preview = None
    meta["expires_at"] = datetime.fromtimestamp(time.time() + FILE_TTL, timezone.utc).isoformat()
    _write_json(meta_path, meta)
    register(UPLOAD_DIR, meta["filename"], meta_path.name, internal_cache_path(source).name)
    return {"status": "success", **meta, "preview": preview}


@app.post("/inspect")
@offload
def inspect_upload(file: UploadFile = File(...)):
    if not file.filename or not is_supported_file(file.filename):
        raise HTTPException(status_code=400, detail="Choose a CSV, XLSX, or XLS file.")
    dataset_id = uuid4().hex
    filename = dataset_id + Path(file.filename).suffix.lower()
    save_upload(UPLOAD_DIR, filename, file.file)
    meta = {"dataset_id": dataset_id, "filename": filename,
            "original_filename": _safe_filename(file.filename), "worksheets": []}
    return _inspect_source(UPLOAD_DIR / f".{dataset_id}.reading.json", meta, ReadingOptions().model_dump())


@app.post("/inspect/{dataset_id}")
@offload
def update_inspection(dataset_id: str, options: ReadingOptions):
    meta_path, meta = _inspection(dataset_id)
    return _inspect_source(meta_path, meta, options.model_dump())


@app.post("/datasets/{dataset_id}/preview")
@offload
def query_dataset_preview(dataset_id: str, request: PreviewQuery):
    _, meta = _inspection(dataset_id)
    if request.revision != meta["revision"] or not meta.get("valid"):
        raise HTTPException(status_code=409, detail="Reading options changed or are invalid. Update the preview first.")
    try:
        if meta.get('preview_schema') and not (request.query or request.sort_column or request.missing_column):
            names = [column['name'] for column in meta['preview_schema']]
            selected = names if request.columns is None else request.columns
            if not selected or len(set(selected)) != len(selected) or set(selected)-set(names):
                raise ValueError('Choose one or more existing, distinct columns.')
            page = (pl.scan_parquet(internal_cache_path(UPLOAD_DIR / meta['filename']))
                    .select(selected).slice(request.offset,request.limit).collect(engine='streaming'))
            result = dataset_preview(page,limit=request.limit)
            result.update(schema=meta['preview_schema'],total_rows=meta['total_rows'],matched_rows=meta['total_rows'],
                          offset=request.offset,row_numbers=list(range(request.offset+1,request.offset+page.height+1)))
            return {'status':'success','revision':meta['revision'],**result}
        frame = read_tabular(internal_cache_path(UPLOAD_DIR / meta["filename"]))
        parameters = request.model_dump(exclude={"revision"})
        return {"status": "success", "revision": meta["revision"], **dataset_preview(frame, **parameters)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/datasets/{dataset_id}/analyze")
@offload
def analyze_inspected(dataset_id: str, request: InspectionConfirmation):
    meta_path, meta = _inspection(dataset_id)
    if request.revision != meta["revision"] or not meta.get("valid"):
        raise HTTPException(status_code=409, detail="Reading options changed or are invalid. Update the preview first.")
    try:
        source = UPLOAD_DIR / meta["filename"]
        frame = read_tabular(internal_cache_path(source))
        result = _analyze_frame(source, meta["filename"], frame, meta.get("protected_text_columns", []))
        meta["confirmed_revision"] = meta["revision"]
        meta["semantics"] = result["semantics"]
        _write_json(meta_path, meta)
        return {**result, "dataset_id": dataset_id, "reading_revision": meta["revision"],
                "display_filename": meta["original_filename"], "reading_options": meta["reading_options"]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/preview/{filename}")
@offload
def get_preview(filename: str):
    safe_name = _safe_filename(filename)
    authorize(UPLOAD_DIR, filename)
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    try:
        processing_source = preferred_processing_source(file_path)
        return read_tabular(processing_source).head(15).to_dicts()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _execute_actions(frame, processing_source, actions, preview=False, audit_path=None):
    engine = CleaningEngine(str(processing_source), audit_path=audit_path)
    engine.df = frame
    if preview:
        engine.lineage_limit = 20

    applied_actions: List[Dict[str, Any]] = []
    # Resolve approved category labels on source text before whitespace/case changes.
    # Coalesce groups so mappings on one column run simultaneously.
    mappings = {}
    ordinary = []
    for item in actions:
        if item.action != "map_categories":
            ordinary.append(item)
            continue
        if not item.approved:
            raise ValueError("Confirm equivalent meaning before mapping categories.")
        combined = mappings.setdefault(item.column, {})
        for old, new in item.mapping.items():
            if old in combined and combined[old] != new:
                raise ValueError(f"Conflicting category decisions for {item.column}.")
            combined[old] = new
    mapped = [CleaningAction(action="map_categories", column=c, mapping=m, approved=True) for c, m in mappings.items()]
    ordered_actions = mapped + sorted(ordinary, key=lambda item: 0 if item.action == "drop_duplicates" else 1)

    roles = suggest_roles(frame)
    protected = {c for c,spec in roles.items() if spec['role'] in {'identifier','phone'}}
    for item in ordered_actions:
        if item.action in {'coerce_numeric','parse_date'} and item.column in protected:
            raise ValueError(f"Conversion blocked for protected identifier: {item.column}. Keep this column as text.")
        action = item.action
        column = item.column
        changed = 0

        if action == "map_categories":
            changed = engine.map_categories(column, item.mapping, item.approved)
        elif action == "trim_whitespace":
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

    return engine, applied_actions


def _preview_annotations(engine):
    rows = set(engine.source_row_ids(15))
    annotations = []
    for entry in engine.preview_audit(rows):
        if entry.get("source_row_id") in rows and entry.get("change_kind") != "removed":
            annotations.append({key: entry[key] for key in ("source_row_id", "column", "change_kind", "method", "old_value", "new_value", "old_is_null", "new_is_null")})
    return annotations


@app.post("/clean")
@offload
def clean_data(request: CleaningRequest):
    """Apply selected actions with cached analysis and lightweight validation."""
    authorize(UPLOAD_DIR, request.filename)
    filename = _safe_filename(request.filename)
    file_path = UPLOAD_DIR / filename
    display_filename = filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on server.")

    decisions = None
    reading_path = UPLOAD_DIR / f".{file_path.stem}.reading.json"
    if reading_path.exists():
        meta = _read_json(reading_path)
        display_filename = meta["original_filename"]
        decisions = meta.get("decisions")
        if decisions and request.decision_revision != decisions["revision"]:
            raise HTTPException(409, "Decisions changed. Review the current decisions before cleaning.")
        if (not meta.get("valid") or request.dataset_id != meta["dataset_id"]
                or request.reading_revision != meta["revision"]
                or meta.get("confirmed_revision") != meta["revision"]):
            raise HTTPException(status_code=409, detail="Reading options changed. Update the preview and analyze again before cleaning.")

    pending_audit = UPLOAD_DIR / f".{uuid4().hex}.audit.pending.csv"
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

        check_shape(working_df)
        rules = decisions.get("rules", []) if decisions else []
        rule_before = evaluate_rules(working_df, rules)
        engine, applied_actions = _execute_actions(working_df, processing_source, request.actions, audit_path=pending_audit)

        output_extension = _clean_output_extension(
            filename,
            engine.df.height,
        )
        output_filename = (
            f"cleaned_{Path(filename).stem}{output_extension}"
        )
        output_path = UPLOAD_DIR / output_filename

        pending_output = output_path.with_name(f".{uuid4().hex}.pending{output_extension}")
        try:
            if not engine.save_cleaned_data(str(pending_output)):
                raise ValueError("The cleaned file could not be saved.")
            export_validation = verify_export(engine.df, pending_output)
            pending_output.replace(output_path)
        finally:
            pending_output.unlink(missing_ok=True)
        mapping_filename = f"{Path(filename).stem}_row_mapping.csv"
        engine.row_mapping().write_csv(UPLOAD_DIR / mapping_filename)
        schema_filename = f"{Path(filename).stem}_schema.json"
        schema_payload = {"source_file": display_filename, "schema": export_validation["schema"],
                          "export": export_validation, "row_mapping_file": mapping_filename,
                          "source_row_id": "1-based record in the confirmed source snapshot; excludes the header and ignored blank records.",
                          "reading_options": meta.get("reading_options") if reading_path.exists() else None}
        _write_json(UPLOAD_DIR / schema_filename, schema_payload)
        after_profile = _lightweight_profile(engine.df)

        validator = ValidationReportingSkill()
        validation = validator.validate(
            before_profile,
            after_profile,
            applied_actions,
            engine.log_entries,
        )

        rule_after = evaluate_rules(engine.df, rules)
        validation["export"] = export_validation
        validation["outcomes"] = engine.outcome_summary()
        validation["custom_rules_before"] = rule_before
        validation["custom_rules_after"] = rule_after
        if rule_after["status"] == "NEEDS_REVIEW":
            validation["status"] = "WARNING"
            validation["warnings"].append("Unresolved rule violations remain; review before use.")

        lineage_filename = (
            f"{Path(filename).stem}_change_log.csv"
        )
        lineage_path = UPLOAD_DIR / lineage_filename
        engine.export_audit(lineage_path)

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
        report_payload["decisions"] = decisions
        _write_json(report_path, report_payload)
        register(UPLOAD_DIR, output_filename, lineage_filename, report_filename, mapping_filename, schema_filename)

        return {
            "status": "success",
            "cleaned_file": output_filename,
            "display_cleaned_file": f"cleaned_{Path(display_filename).stem}{output_extension}",
            "download_url": f"/download/{output_filename}",
            "lineage_download_url": f"/download/{lineage_filename}",
            "report_download_url": f"/download/{report_filename}",
            "row_mapping_download_url": f"/download/{mapping_filename}",
            "schema_download_url": f"/download/{schema_filename}",
            "outcomes": engine.outcome_summary(),
            "preview_source_rows": engine.source_row_ids(15),
            "preview_annotations": _preview_annotations(engine),
            "lineage": engine.get_lineage_summary(),
            "lineage_entries": engine.log_entries,
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
    except HTTPException:
        raise
    except (ValueError, pl.exceptions.PolarsError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        pending_audit.unlink(missing_ok=True)


@app.get("/download/{filename}")
@offload
def download_file(filename: str):
    safe_name = _safe_filename(filename)
    authorize(UPLOAD_DIR, filename)
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
async def health_check():
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
            "semantic roles and explicit validation rules",
            "approved category mappings",
            "full-data dry-run effect preview",
            "private reusable plans",
            "browser-session artifact ownership and expiry",
        ],
    }

# Semantic decisions are revision-bound and never alter the source snapshot.





def _confirmed_frame(dataset_id, revision):
    meta_path, meta = _inspection(dataset_id)
    if (not meta.get('valid') or revision != meta['revision']
            or meta.get('confirmed_revision') != revision):
        raise HTTPException(409, 'Confirm and analyze the current reading first.')
    return meta_path, meta, read_tabular(internal_cache_path(UPLOAD_DIR / meta['filename']))


def _check_decision_revision(meta, revision):
    if meta.get('decisions') and meta['decisions']['revision'] != revision:
        raise HTTPException(409, 'Decisions changed. Review the current decisions first.')


def _decision_plan(meta, frame, roles):
    cached = _read_json(_analysis_cache_path(meta['filename']))
    integrity = dict(cached['integrity'])
    protected = set(integrity.get('protected_columns', [])) | {c for c, r in roles.items() if r['role'] in {'identifier', 'phone'}}
    integrity['protected_columns'] = sorted(protected)
    plan = SafeCleaningSkill().build_cleaning_plan(cached['profile'], integrity)
    return _refine_semantic_plan(frame, roles, plan)


def _refine_semantic_plan(frame, roles, plan):
    for step in plan:
        role = roles.get(step['column'], {}).get('role')
        if step['action'] in {'coerce_numeric','parse_date'}:
            review = conversion_review(frame[step['column']],step['column'],step['action'],step.get('date_format'))
            if role in {'identifier','phone'}:
                review.update(blocked_count=frame[step['column']].len()-frame[step['column']].null_count(), protected_identifier=True, examples=frame[step['column']].drop_nulls().head(5).to_list())
            step.update(conversion_review=review,auto_eligible=False,enabled_by_default=False)
            if review['blocked_count']:
                reason = f"Conversion blocked: {review['blocked_count']} values may lose information. Keep this column as text."
                step.update(executable=False,reason=reason,guided_explanation={'summary':reason},expert_explanation={'summary':reason})
        if step['action'] == 'normalize_casing' or (role in {'identifier', 'phone', 'skills', 'salary', 'date', 'email'} and step['action'] == 'fill_nulls'):
            step.update(auto_eligible=False, enabled_by_default=False)
        if step['action'] == 'fill_nulls' and step.get('strategy') == 'mode' and frame[step['column']].drop_nulls().mode().len() > 1:
            step.update(strategy='keep', auto_eligible=False, enabled_by_default=False,
                        reason='Most-common values are tied; choose explicitly or keep missing.')
    return plan


@app.post('/datasets/{dataset_id}/decisions')
@offload
def update_decisions(dataset_id: str, request: DecisionRequest):
    meta_path, meta, frame = _confirmed_frame(dataset_id, request.revision)
    if set(request.roles) - set(frame.columns):
        raise HTTPException(422, 'A selected role refers to an unknown column.')
    roles = suggest_roles(frame)
    for column, role in request.roles.items():
        roles[column] = {'role': role, 'source': 'user', 'confirmed': True, 'reason': 'Confirmed for this dataset.'}
    rules = [r.model_dump() for r in request.rules]
    try:
        validation = evaluate_rules(frame, rules)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    decisions = {'revision': uuid4().hex, 'roles': roles, 'rules': rules}
    meta['decisions'] = decisions
    _write_json(meta_path, meta)
    return {'status': 'success', 'decisions': decisions, 'rule_validation': validation,
            'cleaning_plan': _decision_plan(meta, frame, roles),
            'category_groups': category_groups(frame, roles, meta.get('protected_text_columns', []))}


@app.post('/datasets/{dataset_id}/effects')
@offload
def preview_effects(dataset_id: str, request: EffectRequest):
    _, meta, frame = _confirmed_frame(dataset_id, request.revision)
    _check_decision_revision(meta, request.decision_revision)
    rules = (meta.get('decisions') or {}).get('rules', [])
    try:
        engine, applied = _execute_actions(frame, internal_cache_path(UPLOAD_DIR / meta['filename']), request.actions, preview=True)
        return {'status': 'success', 'rows_before': frame.height, 'rows_after': engine.df.height,
                'actions': applied, 'sample_changes': engine.get_change_log()[:20], 'outcomes': engine.outcome_summary(),
                'preview_source_rows': engine.source_row_ids(10),
                'rule_validation_before': evaluate_rules(frame, rules),
                'rule_validation_after': evaluate_rules(engine.df, rules), 'preview': engine.df.head(10).to_dicts(),
                'note': 'Full-data dry run. Nothing was exported or changed in the source. Review example changes and totals.'}
    except (ValueError, pl.exceptions.PolarsError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/datasets/{dataset_id}/plans')
@offload
def save_plan(dataset_id: str, request: SavePlanRequest):
    _, meta, frame = _confirmed_frame(dataset_id, request.revision)
    _check_decision_revision(meta, request.decision_revision)
    if not request.approved:
        raise HTTPException(422, 'Approve the decisions before saving them for reuse.')
    decisions = meta.get('decisions')
    if not decisions:
        raise HTTPException(422, 'Review column meanings and rules before saving a plan.')
    try:
        # Validate actions against the current file before remembering any of them.
        _execute_actions(frame, internal_cache_path(UPLOAD_DIR / meta['filename']), request.actions, preview=True)
        payload = {'version': 1, 'schema': {c: str(t) for c, t in frame.schema.items()},
                   'roles': decisions['roles'], 'rules': decisions['rules'],
                   'actions': [a.model_dump(exclude_none=True) for a in request.actions]}
        plan_id = PlanStore(UPLOAD_DIR).save(OWNER.get(), request.name.strip(), payload, request.plan_id)
        return {'status': 'success', 'id': plan_id}
    except (ValueError, pl.exceptions.PolarsError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/datasets/{dataset_id}/plans')
@offload
def list_plans(dataset_id: str):
    _, meta = _inspection(dataset_id)
    schema = meta.get('schema') or {c:str(t) for c,t in pl.read_parquet_schema(internal_cache_path(UPLOAD_DIR / meta['filename'])).items()}
    roles = (meta.get('decisions') or {}).get('roles') or meta.get('semantics') or {}
    return {'status': 'success', 'plans': [dict(p, **compatible_plan(p['payload'], schema, roles))
                                         for p in PlanStore(UPLOAD_DIR).list(OWNER.get())]}


@app.delete('/plans/{plan_id}')
@offload
def delete_plan(plan_id: str):
    if not PlanStore(UPLOAD_DIR).delete(OWNER.get(), plan_id):
        raise HTTPException(404, 'Plan not found.')
    return {'status': 'success'}


@app.delete('/datasets/{dataset_id}')
@offload
def delete_dataset(dataset_id: str):
    _, meta = _inspection(dataset_id)
    # Names are generated by the server, not supplied by the request.
    names = [meta['filename'], f'.{dataset_id}.reading.json', _analysis_cache_path(meta['filename']).name,
             internal_cache_path(UPLOAD_DIR / meta['filename']).name,
             f'{dataset_id}_schema.json', f'{dataset_id}_row_mapping.csv', f'{dataset_id}_cleaning_plan.json', f'{dataset_id}_cleaning_report.json', f'{dataset_id}_change_log.csv']
    names += [f'cleaned_{dataset_id}{ext}' for ext in ('.csv', '.xlsx', '.xls')]
    for name in names:
        (UPLOAD_DIR / name).unlink(missing_ok=True)
    from src.tools.session_manager import registry
    with registry(UPLOAD_DIR) as db:
        db.executemany('DELETE FROM artifacts WHERE name=?', [(name,) for name in names])
    return {'status': 'success'}


# Session identities are opaque browser cookies; no user data is used as an ID.

@asynccontextmanager
async def _lifespan(app):
    async def sweeper():
        while True:
            await asyncio.sleep(60)
            await asyncio.to_thread(cleanup, UPLOAD_DIR)
    await asyncio.to_thread(cleanup, UPLOAD_DIR)
    task = asyncio.create_task(sweeper())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

app.router.lifespan_context = _lifespan
_active_operations = 0
_active_owners = set()
_last_cleanup = 0

@app.middleware('http')
async def browser_session(request, call_next):
    global _active_operations, _last_cleanup
    origin = request.headers.get('origin')
    allowed = os.getenv('DATA_CLEAN_ORIGINS', 'http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:8000,http://localhost:8000').split(',')
    if origin and origin not in allowed:
        return JSONResponse({'detail': 'This website origin is not allowed.'}, status_code=403)
    def early_response(payload, status_code, extra=None):
        headers = {'Cache-Control':'no-store', **(extra or {})}
        if origin:
            headers.update({'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Credentials':'true',
                            'Access-Control-Expose-Headers':'Retry-After','Vary':'Origin'})
        return JSONResponse(payload,status_code=status_code,headers=headers)
    body_limit = MAX_UPLOAD_BYTES+1024*1024 if request.url.path in {'/inspect','/analyze'} else 1024*1024
    try:
        if int(request.headers.get('content-length','0')) > body_limit:
            return early_response({'detail':'Request exceeds the configured size limit.'},413)
    except ValueError:
        return early_response({'detail':'Invalid Content-Length.'},400)
    received = 0
    body_exceeded = False
    original_receive = request._receive
    async def limited_receive():
        nonlocal received, body_exceeded
        message = await original_receive()
        received += len(message.get('body',b''))
        if received > body_limit:
            body_exceeded = True
            raise HTTPException(413,'Request exceeds the configured size limit.')
        return message
    request._receive = limited_receive
    token = request.cookies.get('data_clean_session')
    if not token or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        token = new_token()
    identity = OWNER.set(session_owner(token))
    heavy = request.method in {'POST','DELETE'} or request.url.path.startswith('/preview/')
    entered = False
    try:
        if time.time() - _last_cleanup > 60:
            _last_cleanup = time.time()
            await asyncio.to_thread(cleanup, UPLOAD_DIR)
        if heavy:
            if _active_operations >= MAX_OPERATIONS or OWNER.get() in _active_owners:
                return early_response({'detail': 'The server is busy. Try again shortly.', 'code':'server_busy'},429,{'Retry-After':str(RETRY_AFTER)})
            _active_operations += 1
            _active_owners.add(OWNER.get())
            entered = True
        response = await call_next(request)
        if body_exceeded:
            return early_response({'detail':'Request exceeds the configured size limit.'},413)
        response.set_cookie('data_clean_session', token, httponly=True, samesite='strict',
                            secure=os.getenv('DATA_CLEAN_SECURE_COOKIE', '0') == '1', max_age=90 * 86400)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    finally:
        if entered:
            _active_operations -= 1
            _active_owners.discard(OWNER.get())
        OWNER.reset(identity)


@app.get('/limits')
async def deployment_limits():
    return {'status':'success','limits':public_limits()}


@app.get('/', include_in_schema=False)
@offload
def website():
    # Serve UI and API from one origin so strict session cookies work in hosting.
    html = (Path(__file__).parent/'index.html').read_text(encoding='utf-8')
    html = html.replace('<head>','<head><script>window.DATA_CLEAN_API_BASE=location.origin;</script>',1)
    return HTMLResponse(html)

app.mount('/assets',StaticFiles(directory=Path(__file__).parent/'assets'),name='assets')
