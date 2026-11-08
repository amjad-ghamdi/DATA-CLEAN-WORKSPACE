# DATA CLEAN V1.2.1

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
