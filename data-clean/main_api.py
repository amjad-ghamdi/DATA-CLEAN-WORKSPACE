from pathlib import Path
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
from src import workflow_service as workflow
from src.workflow_service import (
    _safe_filename, _read_json, _write_json, _total_missing,
    _lightweight_profile, _clean_output_extension, _execute_actions,
    _preview_annotations, _refine_semantic_plan,
)
from src.tools.column_semantics import suggest_roles, category_groups
from src.tools.validation_rules import evaluate_rules
from src.tools.cleaning_plans import PlanStore, compatible_plan
from src.tools.session_manager import OWNER, session_owner, new_token, register, authorize, cleanup, save_upload, check_shape, FILE_TTL
from src.tools.dataset_preview import dataset_preview
from src.tools.tabular_io import (
    internal_cache_path,
    is_supported_file,
    preferred_processing_source,
    read_tabular,
    read_with_options,
    TabularReadError,
    write_internal_cache,
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


def _analysis_cache_path(filename: str) -> Path:
    return workflow._analysis_cache_path(filename, UPLOAD_DIR)


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
    return workflow.analyze_frame(file_path, filename, df, protected_text_columns, storage_dir=UPLOAD_DIR)


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


@app.post("/clean")
@offload
def clean_data(request: CleaningRequest):
    """Apply selected actions with cached analysis and lightweight validation."""
    return workflow.clean_dataset(UPLOAD_DIR, request)


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
    return workflow.decision_plan(UPLOAD_DIR, meta, frame, roles)


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
