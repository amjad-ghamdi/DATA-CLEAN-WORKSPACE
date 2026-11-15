"""Dataset processing orchestration shared by API routes.

The storage root is supplied per request. This module never owns admission limits
or browser-session state; existing ownership checks use the caller's context.
"""
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4
import json

import polars as pl
from fastapi import HTTPException

from src.api_models import CleaningAction, CleaningRequest
from src.recommendations import SafeCleaningSkill
from src.skills.validation_reporting_skill import ValidationReportingSkill
from src.tools.cleaning_engine import CleaningEngine
from src.tools.quality_analyzer import QualityAnalyzer
from src.tools.integrity_checker import IntegrityChecker
from src.tools.column_semantics import ROLES, suggest_roles, category_groups
from src.tools.validation_rules import evaluate_rules
from src.tools.session_manager import authorize, check_shape, register
from src.tools.tabular_io import (
    LARGE_DATASET_THRESHOLD, internal_cache_path, output_extension_for,
    preferred_processing_source, read_tabular, write_internal_cache,
    verify_export, conversion_review,
)


def _safe_filename(filename: str) -> str:
    return Path(filename).name


def _analysis_cache_path(filename: str, storage_dir: Path) -> Path:
    safe_name = _safe_filename(filename)
    return storage_dir / f".{Path(safe_name).stem}.analysis.json"


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


def analyze_frame(file_path: Path, filename: str, df: pl.DataFrame, protected_text_columns=None, *, storage_dir: Path):
    check_shape(df)
    analysis_cache = _analysis_cache_path(filename, storage_dir)
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

    register(storage_dir, filename, analysis_cache.name, internal_cache_path(file_path).name)
    plan_filename = f"{Path(filename).stem}_cleaning_plan.json"
    plan_path = storage_dir / plan_filename
    _write_json(
        plan_path,
        {
            "source_file": filename,
            "generated_by": "DATA CLEAN V1.2.1",
            "processing": profile.get("processing", {}),
            "plan": cleaning_plan,
        },
    )

    register(storage_dir, plan_filename)
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


def clean_dataset(storage_dir: Path, request: CleaningRequest):
    """Apply selected actions with cached analysis and lightweight validation."""
    authorize(storage_dir, request.filename)
    filename = _safe_filename(request.filename)
    file_path = storage_dir / filename
    display_filename = filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on server.")

    decisions = None
    reading_path = storage_dir / f".{file_path.stem}.reading.json"
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

    pending_audit = storage_dir / f".{uuid4().hex}.audit.pending.csv"
    try:
        processing_source = preferred_processing_source(file_path)
        working_df = read_tabular(processing_source)

        analysis_cache = _analysis_cache_path(filename, storage_dir)
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
        output_path = storage_dir / output_filename

        pending_output = output_path.with_name(f".{uuid4().hex}.pending{output_extension}")
        try:
            if not engine.save_cleaned_data(str(pending_output)):
                raise ValueError("The cleaned file could not be saved.")
            export_validation = verify_export(engine.df, pending_output)
            pending_output.replace(output_path)
        finally:
            pending_output.unlink(missing_ok=True)
        mapping_filename = f"{Path(filename).stem}_row_mapping.csv"
        engine.row_mapping().write_csv(storage_dir / mapping_filename)
        schema_filename = f"{Path(filename).stem}_schema.json"
        schema_payload = {"source_file": display_filename, "schema": export_validation["schema"],
                          "export": export_validation, "row_mapping_file": mapping_filename,
                          "source_row_id": "1-based record in the confirmed source snapshot; excludes the header and ignored blank records.",
                          "reading_options": meta.get("reading_options") if reading_path.exists() else None}
        _write_json(storage_dir / schema_filename, schema_payload)
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
        lineage_path = storage_dir / lineage_filename
        engine.export_audit(lineage_path)

        report_filename = (
            f"{Path(filename).stem}_cleaning_report.json"
        )
        report_path = storage_dir / report_filename

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
        register(storage_dir, output_filename, lineage_filename, report_filename, mapping_filename, schema_filename)

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


def decision_plan(storage_dir: Path, meta, frame, roles):
    cached = _read_json(_analysis_cache_path(meta['filename'], storage_dir))
    integrity = dict(cached['integrity'])
    protected = set(integrity.get('protected_columns', [])) | {c for c, r in roles.items() if r['role'] in {'identifier', 'phone'}}
    integrity['protected_columns'] = sorted(protected)
    plan = SafeCleaningSkill().build_cleaning_plan(cached['profile'], integrity)
    return _refine_semantic_plan(frame, roles, plan)
