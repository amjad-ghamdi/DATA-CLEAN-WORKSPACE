# DATA CLEAN — technical guide

[Project overview](../README.md) ·
[Live demo](https://data-clean-workspace.onrender.com/)

DATA CLEAN assesses tabular data, builds an explainable cleaning plan and applies
only the treatments selected by the user. It serves the interface and API from one
origin; no separate frontend build or Node.js server is required.

## Run locally

Use a supported Python installation with a virtual environment. Development and
regression checks were run on Python 3.14. Commands below are for Windows PowerShell.
From the repository root:

```powershell
cd data-clean
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:DATA_CLEAN_PROFILE='local'
.\.venv\Scripts\python.exe -m uvicorn main_api:app --host 127.0.0.1 --port 8000 --workers 1
```

Open **http://127.0.0.1:8000/**. Keep the terminal open; Ctrl+C stops the server.
On macOS/Linux, use `.venv/bin/python` and `export DATA_CLEAN_PROFILE=local`.

The generated sample is [`assets/samples/demo.csv`](assets/samples/demo.csv).
It contains 30 synthetic records with whitespace, category-label variations and
missing scores. Upload it through the interface to explore the workflow.

## Workflow and decisions

1. Choose a file and request its reading preview.
2. Check columns/text; adjust CSV settings or the Excel worksheet/header if needed.
3. Confirm the interpretation and analyze the dataset.
4. Review actions in Default or Expert mode. Ordinary action selections are kept
   independently per mode; approved category mappings and loaded decisions can
   be shared. Row deletion and guarded conversions require explicit review.
5. Optionally confirm column meanings, add rules or load a compatible saved plan.
6. Preview selected effects, then apply and download the outputs.

Changing reading options invalidates older analysis. Changed meanings/rules must
be confirmed before affected operations. Plans load unchecked for review, and
statistical treatments recompute from the current file. Unresolved issues do not
force a guessed replacement or prevent downloading an otherwise valid result.

## Runtime profiles

| Setting | Local (default) | Demo |
| --- | ---: | ---: |
| File size | 100 MiB | 5 MiB |
| Data rows, excluding header | 2,000,000 | 25,000 |
| Columns | 500 | 50 |
| Heavy operations per process | 2 | 1 |
| Registered artifact expiry | 24 hours | 1 hour |
| Expanded XLSX content | 512 MiB | 32 MiB |

These are configurable application limits, not guaranteed processing capacity.
A file must satisfy every limit. Keep **one Uvicorn worker**: the admission limit
is process-local, not a distributed lock. Preview pages contain at most 200 rows.
Some expensive semantic checks use a sample on large datasets; core missing and
duplicate counts and cleaning use the full dataset.

To run the restricted profile, stop the API and restart it with:

```powershell
$env:DATA_CLEAN_PROFILE='demo'
.\.venv\Scripts\python.exe -m uvicorn main_api:app --host 127.0.0.1 --port 8000 --workers 1
```

Check **http://127.0.0.1:8000/limits** for the effective settings.
[`.env.example`](.env.example) documents the environment variables; it is **not
loaded automatically**. Existing `DATA_CLEAN_MAX_*` and retention overrides take
precedence over the profile. Remove old overrides when switching profiles.

## Outputs and interpretation

- Cleaned CSV/Excel data; large outputs may use CSV for faster export.
- JSON validation report with before/after metrics and rule findings.
- Full change-log CSV with original source-record IDs and old/new values.
- Output-to-source row mapping and schema/export notes.

An internal source ID tracks the confirmed data record through row deletions.
It excludes headers/ignored blank records and is not added to the cleaned dataset.
Dry runs show bounded examples and full totals. Filled/edited cells are labelled.
The final audit is written in batches and can be much larger than its preview.

Export verification reopens the generated artifact and checks its structure and
values. A PASS result does not establish factual accuracy. CSV consumers may infer
other types; consult the schema manifest. Excel empty-text versus blank-cell
limitations are disclosed in the export report. Audit logs support traceability,
not an automatic undo endpoint.

## Storage, privacy and hosting

- Artifacts and plans belong to an opaque browser-session cookie. This is not an
  account/login system or cross-device identity.
- Uploads/results have configurable expiry. Saved plans remain until deleted or
  storage is lost; they can contain user-provided mappings/constants.
- Set `DATA_CLEAN_STORAGE_DIR` to a private directory outside served assets.
- On Render Free, filesystem changes can disappear on sleep, restart or redeploy,
  earlier than configured expiry. Do not rely on the demo for persistent storage.
- Use explicit allowed origins and secure cookies for HTTPS. The interface offers
  manual retry for capacity errors; requests are not queued automatically.
- Uploaded files are processed by the server, not exclusively inside the browser.
  Avoid uploading sensitive data to the public demo.

For Render, set Root Directory to `data-clean` and use:

```text
Build: pip install -r requirements.txt
Start: uvicorn main_api:app --host 0.0.0.0 --port $PORT --workers 1
Health Check Path: /health
```

Environment variables:

```text
DATA_CLEAN_PROFILE=demo
DATA_CLEAN_SECURE_COOKIE=1
DATA_CLEAN_STORAGE_DIR=/tmp/data-clean
DATA_CLEAN_ORIGINS=https://YOUR-SERVICE.onrender.com
```

Replace the origin with the real HTTPS address, without a trailing slash.
No paid disk or separate database is required for this temporary demo.

## Tests and benchmarks

In the activated virtual environment, or using its Python executable:

```powershell
.\.venv\Scripts\python.exe -m pip install pytest httpx
.\.venv\Scripts\python.exe -m pytest tests -q
```

Recorded local result after refactoring: **91 tests and 12 subtests passed**.
The demo-safety subset passed 18 tests. Actual boundary checks accepted 25,000
rows, 50 columns and 5 MiB, and rejected exceeding each boundary.
Browser/API acceptance covered decisions, plans, original-row tracking, read-back
validation, Arabic/English, themes and mobile workflow.

With the API running, benchmark an existing dataset from another terminal:

```powershell
.\.venv\Scripts\python.exe benchmark.py path/to/input.csv --runs 3 --output benchmark_results.csv
```

This developer tool measures analyze/clean HTTP timing and records validation
counts; it does not prove cleaning accuracy. Supply your own benchmark datasets.
The historical local 1M-row test used three columns and is not a Render benchmark.

## Code organization

```text
main_api.py                    Routes, session/admission middleware and hosting
main.py                        Optional read-only CLI
src/api_models.py              Request contracts and validation limits
src/workflow_service.py        Analysis, cleaning, validation and export orchestration
src/recommendations.py         Deterministic recommendations, explanations and plans
src/skills/                    Quality reporting and before/after validation
src/tools/                     File I/O, previews, engine, rules, sessions and plans
assets/js/app.js               State, rendering, grouped events and single startup
assets/js/i18n.js              Translation and appearance preferences
assets/css/styles.css          Responsive layout, typography and themes
tests/                         Functional, safety, traceability and performance tests
```

Optional CLI, using the same virtual-environment Python:

```powershell
.\.venv\Scripts\python.exe main.py assets/samples/demo.csv --task both
```

## Main API routes

| Route | Purpose |
| --- | --- |
| `GET /` | Interface |
| `GET /health`, `GET /limits` | Health and effective resource profile |
| `POST /inspect` | Upload and inspect |
| `POST /inspect/{dataset_id}` | Change file interpretation |
| `POST /datasets/{dataset_id}/preview` | Paged search/filter/sort |
| `POST /datasets/{dataset_id}/analyze` | Confirm reading and analyze |
| `POST /datasets/{dataset_id}/decisions` | Confirm roles and rules |
| `POST /datasets/{dataset_id}/effects` | Full-data dry run |
| `POST /clean` | Apply reviewed actions |
| `GET /download/{filename}` | Authorized artifact download |
| `POST` / `GET /datasets/{dataset_id}/plans` | Save/list compatible private plans |
| `DELETE /plans/{plan_id}` | Delete a saved plan |
| `DELETE /datasets/{dataset_id}` | Delete registered dataset artifacts |

The legacy `POST /analyze` and `GET /preview/{filename}` routes remain available.
Interactive API documentation is served at `/docs`.
