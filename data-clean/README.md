# DATA CLEAN V1.2.1

## Stage 1 reading confirmation and preview

Workspace now follows **Choose file → Preview file → Confirm reading → Analyze**.
This stage changes file interpretation and previewing, not the later custom-rule,
reusable-plan or session-isolation features.

- CSV automatically suggests delimiter and encoding. Override comma, semicolon,
  tab or pipe and text encoding, choose the header record, then **Update preview**.
  Encoding detection is a suggestion, not a guarantee; verify non-English text.
- Excel lists worksheets and lets you choose the sheet and header row. The header
  must have unique, nonempty names. Numeric Excel display formats cannot recover
  zeros that were never stored as text. CSV header numbers refer to logical CSV
  records; Excel header numbers refer to worksheet rows.
- The preview shows 50 rows per page, typed columns, full-column missing and
  empty-string counts, and original data-record numbers. Search (literal,
  case-insensitive), sorting and missing-value filters operate on the **full
  dataset**, even when some columns are hidden. **Apply view** resets paging.
- Quoted empty CSV text is distinguished from an unquoted missing field. Entirely
  blank records are reported as ignored; inconsistent field counts or invalid
  quoting block analysis rather than silently skipping records. Invalid initial
  interpretation retains the upload so you can change settings and retry.
- Identifier-like columns, leading-zero values, long numeric text, ambiguous date
  strings and comma-formatted numeric text stay text. Their transformations are
  flagged high impact in the existing planner rather than auto-applied. Simple
  numbers are inferred conservatively. Expert-selected conversions still require
  care; this is not a complete schema/column-role editor.
- Check the explicit reading confirmation before analysis. The server uses the
  same internal Parquet snapshot for analysis and cleaning. Changing reading
  settings invalidates old analysis/clean requests, including after a failed
  re-read. The source upload is not rewritten.

The frontend uses these additive API routes:

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/inspect` | Multipart upload, internal dataset ID and initial interpretation |
| POST | `/inspect/{dataset_id}` | Update delimiter/encoding/header/sheet and revision |
| POST | `/datasets/{dataset_id}/preview` | Bounded page with full-dataset query parameters |
| POST | `/datasets/{dataset_id}/analyze` | Analyze the explicitly confirmed reading revision |

Preview/analyze requests supply the returned `revision`. Cleaning an inspected
upload supplies `filename`, `dataset_id`, `reading_revision` and `actions`.
Original multipart `/analyze`, `/clean`, `/preview` and `/download` routes remain
available for existing workflows; `/analyze` now also uses the stricter reader.
The benchmark retains its original upload/analyze workflow.

Install updated dependencies and run the API and static frontend as described
below. Restart the API after updating and hard-refresh the browser.

Run regression tests from `data-clean`:

```powershell
python -m pip install pytest httpx
python -m pytest tests -q
```

`tests/test_quality_workflow.py` covers strict CSV reading, Arabic and UTF-16,
Excel sheet/header selection, precision and leading zeros, quoted/multiline
fields, malformed records, full-dataset search, pagination, confirmation
revisions, API requests, cached interpretation and cleaned downloads.

### Current scope and limits

This remains a **local, single-worker development application**. Random dataset
IDs avoid filename collisions but are not user authentication or session
isolation. Existing CORS and download behavior do not make it suitable for
untrusted public uploads. File isolation, resource quotas, expiry and public
deployment protections remain a later stage. Do not expose this version publicly.

Strict inspection reads and validates the whole file and creates a Parquet
snapshot. Search/sort/filter also process the full snapshot per request; only the
returned page is bounded (50 in the UI, 200 maximum in the API). This is not an
out-of-core preview engine or a new million-row performance guarantee. Capacity
and performance tuning remain separate from the correctness checks in this stage.

DATA CLEAN is a risk-aware data-cleaning workspace that profiles CSV and Excel datasets, ranks cleaning recommendations, applies approved transformations, and validates the result.

## Supported files

- CSV (`.csv`)
- Excel (`.xlsx`)
- Legacy Excel (`.xls`) input; cleaned output is normalized to `.xlsx`

## Performance changes in V1.2.1

- **Single ingest per request** — the uploaded dataset is loaded once, then the same Polars DataFrame is shared with profiling, integrity checks, preview generation, and planning.
- **Large Dataset Mode** — automatically activates at 200,000 rows.
- **Sampled expensive checks** — category inconsistencies, type-detection heuristics, and IQR bound estimation use a deterministic sample of up to 50,000 rows on large datasets.
- **Full-data core checks** — row count, missing-value counts, duplicate hash counts, and outlier counts still evaluate the complete loaded dataset.
- **Polars streaming input** — CSV and internal Parquet reads use Polars lazy/streaming collection rather than Python row loops.
- **Excel Parquet cache** — after the first Excel parse, DATA CLEAN stores an internal Parquet copy so cleaning and preview operations do not need to parse the large workbook again.
- **Shared integrity analysis** — identifier detection reuses the already loaded DataFrame and combines candidate metrics in a single Polars select.
- **Processing metadata** — API responses report the processing mode, sample size, and whether an internal cache was used.

The goal is to reduce repeated full-file work while preserving full-dataset cleaning.

### Cleaning performance

- **Reuse the analyze profile** — `/clean` reads the profile cached by `/analyze` instead of running the full semantic profiler again before cleaning.
- **Lightweight post-clean validation** — after cleaning, DATA CLEAN recalculates exact rows, missing values, schema, and duplicate counts without rerunning outlier/category/type recommendation analysis.
- **Vectorized lineage** — changed rows are detected with Polars expressions instead of converting million-row columns to Python lists and comparing them row by row.
- **Fast large export** — datasets with 200,000+ rows are exported as CSV by default, avoiding the much slower XLSX writer. The original uploaded file remains preserved.


## V1.1 safety and UX features retained

- Guided mode is the default UI.
- Expert mode exposes technical controls.
- Apply Recommended Plan only applies eligible recommendations.
- Context-aware missing-value handling.
- Reviewed outlier handling.
- Schema validation and lineage.
- CSV and Excel export behavior.

## Large Dataset Mode

By default:

```text
< 200,000 rows
    Standard mode

>= 200,000 rows
    Large Dataset Mode
    ├─ full dataset: rows, missing values, duplicate hash counts
    ├─ full dataset: final outlier counts
    └─ sample up to 50,000 rows: expensive semantic profiling
```

Sampling is used only to speed profiling decisions. Cleaning actions still apply to the full dataset.

## Architecture

- `src/tools/tabular_io.py` — streaming reads and Excel-to-Parquet internal cache
- `src/tools/quality_analyzer.py` — standard / large-dataset profiling
- `src/tools/integrity_checker.py` — identifier and integrity checks
- `src/agent/recommendation_engine.py` — ranked recommendations and context rules
- `src/agent/explanation_agent.py` — user-facing explanations
- `src/skills/safe_cleaning_skill.py` — executable cleaning plan
- `src/tools/cleaning_engine.py` — deterministic transformations and lineage
- `src/skills/validation_reporting_skill.py` — before/after validation
- `main_api.py` — FastAPI workflow
- `index.html` — Guided / Expert interface

## Run locally

From the `data-clean` directory:

```powershell
python -m pip install -r requirements.txt
python -m uvicorn main_api:app --reload
```

In a second terminal:

```powershell
python -m http.server 5500
```

Open:

```text
http://127.0.0.1:5500/index.html
```

## Tests

```powershell
python -m pytest
```

Performance-focused tests cover preloaded DataFrame reuse, automatic Large Dataset Mode, hash-based duplicate counting, and internal Parquet caching.


## Editorial interface

The English interface uses a left sidebar with About DATA CLEAN (the default
home), Workspace, and User Guide. The ivory/forest-green design uses three
self-hosted font families, so it needs no font service or CDN at runtime.

- Choose a file, then select **Analyze file**. Merely choosing a file does not upload it.
- **Guided Mode** is the default. It lists auto-eligible, executable recommendations
  except high-impact actions, keep strategies, and constant fills requiring manual input.
- **Expert Mode** exposes the full plan, settings, constant values, and impact labels.
  The two modes retain independent selections and Expert settings while switching.
- Review the optional analysis/preview disclosure, apply the selected actions, and
  download the cleaned file, validation report, and CSV change log.
- Navigation between the three pages preserves the in-memory session. Refreshing
  or closing the page clears it; there is no account or browser-persistent history.

Serve the entire `data-clean` directory (not just `index.html`):

```powershell
python -m uvicorn main_api:app --host 127.0.0.1 --port 8000
```

In another terminal in the same directory:

```powershell
python -m http.server 5500
```

Open `http://127.0.0.1:5500/index.html`. The frontend calls
`http://127.0.0.1:8000`; change `API_BASE` in `assets/js/app.js` if the API
runs elsewhere. The interface requires JavaScript and a modern browser.

Frontend files:

- `index.html`: accessible page structure and content
- `assets/css/styles.css`: responsive layout, local fonts, focus and reduced-motion styles
- `assets/js/app.js`: navigation, session state, Guided/Expert controls and HTTP workflow
- `assets/fonts/`: Libre Baskerville, IBM Plex Sans, Source Sans 3, and their SIL Open Font Licenses

The API, cleaning engine and benchmark are unchanged by this interface update.
Quality checks are advisory; inspect the validation report and verify the result
against your data requirements. Existing backend parsing/cleaning limitations
still apply.
