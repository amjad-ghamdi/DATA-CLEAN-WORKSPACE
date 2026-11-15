# DATA CLEAN V1.2.1

## Safety and resource profiles

Local defaults retain the existing 2,000,000-row capacity. To prepare a small
portfolio demo, set `DATA_CLEAN_PROFILE=demo` **before starting** the API:

| Setting | local (default) | demo (initial conservative limits) |
|---|---:|---:|
| Upload size | 100 MiB | 5 MiB |
| Rows | 2,000,000 | 25,000 |
| Columns | 500 | 50 |
| Active processing requests | 2 | 1 |
| File retention | 24 hours | 1 hour |
| Expanded XLSX contents | 512 MiB | 32 MiB |

These are configurable guardrails, **not a proven capacity guarantee for a free
host**. Verify memory/time limits on the chosen hosting service before release.
The interface fetches `/limits`, shows active file limits before upload and offers
a small synthetic sample CSV. Server-side checks enforce the same limits; file
shape/decoded Excel limits are also checked. Multipart requests allow 1 MiB of
envelope overhead; other request bodies are capped at 1 MiB. Declared and chunked
oversized requests are rejected. CSV row/column limits apply before loading into
Polars; Excel reads stop at the configured row bound plus one detection row.

PowerShell, from `data-clean`:

```powershell
$env:DATA_CLEAN_PROFILE='demo'
python -m uvicorn main_api:app --host 127.0.0.1 --port 8000 --workers 1
```

Open `http://127.0.0.1:8000/#workspace` to serve both the site and API from one
origin. The previous separate UI on port 5500 still works. Use the local profile
for large-file tests (`$env:DATA_CLEAN_PROFILE='local'` and restart the process).

Hosting configuration: use `DATA_CLEAN_PROFILE=demo`, **one worker**, an explicit
`DATA_CLEAN_ORIGINS=https://YOUR-HOST` and `DATA_CLEAN_SECURE_COOKIE=1` with HTTPS.
Start `uvicorn main_api:app --host 0.0.0.0 --port $PORT --workers 1` (hosting shell).
Serve the site from the same host to preserve strict session-cookie behavior.
Keep `DATA_CLEAN_STORAGE_DIR` private, outside public assets. Override individual
`DATA_CLEAN_MAX_UPLOAD_MB`, `DATA_CLEAN_MAX_ROWS`, `DATA_CLEAN_MAX_COLUMNS`,
`DATA_CLEAN_MAX_OPERATIONS`, `DATA_CLEAN_FILE_TTL_HOURS` or
`DATA_CLEAN_MAX_EXPANDED_MB` only after testing. The process-level gate is not a
distributed limit; multiple workers/replicas require shared admission control.

### What changed

- Numeric/date conversions run full-column checks before mutation. Invalid or
  nonfinite values, formatted numeric text, leading zeros, precision-sensitive
  digits and identifier/phone columns cannot be silently coerced. Date conversion
  rejects time/timezone loss and ambiguous date order without an explicit format.
  Blocked proposals show counts/examples and keep the column as text. Type
  conversions never start selected; valid conversions remain available for review.
- Strict CSV validation retains original quoting and empty-versus-null semantics,
  buffering one logical record into a temporary UTF-8 file. Delimiter sniffing
  uses a bounded sample. Analysis reuses/writes a Parquet snapshot for CSV too;
  ordinary unfiltered preview pages scan only their requested window/columns.
  Saved-plan compatibility uses cached schema/roles instead of rereading all rows.
- HTTP processors run in worker threads with browser ownership context preserved.
  Health/limit requests remain responsive. At capacity, admission returns HTTP
  429 with `code=server_busy` and `Retry-After`, including allowed-origin CORS
  headers. One browser session cannot mutate two workflows simultaneously.
  Rejected requests are **not queued**. The UI retains the file/choices and offers
  a manual retry; it does not poll or retry automatically. Admission is released
  when work completes or fails.
- API cleaning writes the full audit to disk in bounded batches and retains only
  20 Python audit examples. Full totals, per-action summaries, original row
  mapping and the complete downloadable CSV remain available. Preview annotations
  scan only the needed record IDs. Direct library engines keep their existing
  in-memory log unless an `audit_path` is supplied; `get_change_log()` is then a
  sample and `log_entries` is the full count. Explicit `get_lineage_df()` still
  materializes the log; HTTP export does not call it.

Local measurement on 200,000 synthetic rows / 220,000 audit entries: peak process
working set fell from 338.46 to 135.74 MiB; output hashes and audit totals matched.
The measured action/audit step took 1.12 s with the old memory log versus 2.64 s
with disk logging. This is a memory improvement with an IO/CPU tradeoff, not a
universal speedup or a production concurrency benchmark.

Validation for this processing update: **90 tests passed plus 12 subtests** (one
installed Starlette/httpx test-client deprecation warning). Real Edge/API checks
passed for separate-port local UI and same-origin hosted UI, both languages,
unchanged dataset values, manual busy retry without polling, changing mode before
retry, sample data, stored preferences and client upload limits. A fresh local
1,000,000-row / 3-column check retained leading-zero IDs, wrote 1,100,000 complete
audit entries while keeping 20 Python examples, and passed export read-back.
That synthetic check took 15.24 s including CSV generation, reading, actions,
audit writing, output and verification, with peak working set 335.68 MiB. It is
not the earlier `/clean` request latency and is not a measurement on free hosting.

Existing saved plans can no longer force unsafe conversions. Technical JSON and
download formats remain machine-readable; interface warnings are bilingual.

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
  care. The optional column-role editor is described below.
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

This is a **single-worker application** with browser-session file ownership,
explicit allowed origins, bounded uploads, and expiring temporary artifacts.
Browser cookies are not accounts or cross-device authentication. Public deployment
still requires HTTPS, a same-site frontend/API arrangement, host-level request and
memory limits, a private persistent storage volume, and deployment load testing.
The static web server must serve only this source directory, never the storage
folder. Use the same hostname (`127.0.0.1`) for both local services.

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

- Choose a file, select **Preview file**, confirm its reading, then analyze. Choosing a file alone does not upload it.
- **Default Mode** is the default. It lists auto-eligible, executable recommendations
  except high-impact actions, keep strategies, constant fills and row deletion.
  **Needs your decision** exposes unchecked per-issue treatments with simple questions.
  Guided review settings and selections are separate from Expert settings.
- **Expert Mode** exposes the full plan, settings, constant values, and impact labels.
  The two modes retain independent selections and Expert settings while switching.
- Review the optional analysis/preview disclosure, apply the selected actions, and
  download the cleaned file, validation report, and CSV change log.
- Navigation between the three pages preserves the in-memory session. Refreshing
  or closing the page clears the active dataset view. Approved plans persist on the API server, scoped to the browser cookie. There are no accounts or cross-device history.

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

Quality checks are advisory; inspect the validation report and verify the result
against your data requirements. Existing backend parsing/cleaning limitations
still apply.


## Semantic decisions, validation and reusable plans

The implementation is deterministic. There is no LLM service, external upload,
agent execution or model training. No new runtime dependency is required for
these features: the private plan store uses Python's built-in SQLite.

1. Analyze a confirmed file. Expand **Column meanings and validation rules**.
   Known name aliases and bounded value samples suggest identifiers, emails,
   phones, ratings, salaries, dates, categories, skills, text and numbers. These
   are suggestions, not verified facts. Unknown columns remain unspecified.
2. Correct meanings and add explicit rules: required, unique, numeric range,
   allowed values, email syntax, date format or text length. Rating scales are
   supplied by the user, not inferred as universally 0–5. Missing entries are
   ignored by value-specific checks; use Required when missing is a violation.
3. Select **Confirm meanings and check rules**. Checks cover the full dataset,
   provide exact violation counts and up to five examples per rule. Counts can
   overlap. Rules only flag values; unresolved cases remain unchanged.
4. Category suggestions compare case, Unicode compatibility and whitespace.
   They do not prove semantic equivalence or convert units. For each group,
   choose a label and explicitly approve equivalent meaning. The most frequent
   label is only a proposed target; ties have no default target. Identifier and
   reading-protected columns are excluded. Candidate discovery is bounded to
   500 distinct values per column and 100 groups, so absence of a suggestion is
   not proof that labels are consistent. Arbitrary fuzzy matching is not used.
5. **Preview selected changes** runs the same action executor on the full frame,
   without writing an output. It returns totals and up to 20 lineage examples.
   Custom rules are checked before and after; existing source files stay intact.
   Preview and lineage carry stable original source record IDs, preserved after
   duplicate removal and row filtering. IDs refer to data records, not physical CSV lines. The final structural PASS is
   separate from business-rule checks; remaining violations produce WARNING.
6. Open **Saved plans and remembered decisions**, enter a name and approve saving.
   Only explicitly saved roles/rules/actions are remembered; files do not teach
   the system automatically. Stored category mappings and constants can contain
   sensitive values: review them before saving. Plans are scoped to the browser
   session, limited to 100 per session, and can be loaded, updated or deleted.
7. On another file, the plan list checks column names, storage types and role
   compatibility. Load for review; all loaded actions start unchecked. Schema
   compatibility does not establish matching business meaning. Select the desired
   remembered actions, preview, then apply. Means, medians and modes recompute
   from the new file. Explicit constants, mappings and outlier bounds remain
   fixed and need review. A tied mode fails clearly rather than picking a label
   arbitrarily. No external reference lookup or factual verification is claimed.

Guided and Expert keep independent ordinary-action selections. Column roles,
validation rules and explicitly approved category decisions describe the dataset
and are shared between modes. Changing confirmed decisions regenerates the plan
and resets its ordinary selections; it does not silently run a saved recipe.

### API additions

- `POST /datasets/{id}/decisions`: reading revision, role overrides, rules;
  returns a decision revision, full-data findings, refreshed suggestions/groups.
- `POST /datasets/{id}/effects`: reading/decision revisions and selected actions;
  dry run using the same executor as `/clean`.
- `POST /datasets/{id}/plans`: revisions, name, selected actions, `approved: true`,
  optional `plan_id` to update an owned plan.
- `GET /datasets/{id}/plans`: owned plans and compatibility results.
- `DELETE /plans/{id}`: delete an owned plan.
- `DELETE /datasets/{id}`: delete the upload, snapshot and generated artifacts.
- `/clean` also supplies `decision_revision` after meanings/rules are confirmed.
- Explicit category action: `action: "map_categories"`, `column`, `mapping`,
  `approved: true`. Mappings for the same column are combined and applied
  simultaneously before ordinary formatting; conflicting targets are rejected.

All HTTP file routes require the owning cookie. Scripts must reuse one
`requests.Session()`; `benchmark.py` already does so. Use `credentials: 'include'`
for browser API calls. Legacy `/analyze` assigns a generated filename to prevent
collisions and still supports the benchmark's analyze → clean sequence.

### Storage and security settings

`.env.example` documents settings; it is not automatically loaded. Set environment
variables before starting Uvicorn (PowerShell: `$env:NAME = 'value'`). Defaults:

- `DATA_CLEAN_STORAGE_DIR`: operating-system temp directory / `data_clean_uploads`;
  set a private persistent directory outside the static source tree for deployment.
- `DATA_CLEAN_MAX_UPLOAD_MB=100`, `DATA_CLEAN_MAX_ROWS=2000000`,
  `DATA_CLEAN_MAX_COLUMNS=500`: limits, not a throughput guarantee. Row/column
  checks occur after parsing; host memory limits are still necessary for hostile
  files. A million rows is permitted but capacity depends on width and hardware.
- `DATA_CLEAN_FILE_TTL_HOURS=24`: expiry checked on access; cleanup at startup,
  on requests and every 60 seconds while running. After shutdown, cleanup resumes
  at next startup. Deletion is ordinary filesystem deletion, not secure erasure.
- Up to 20 active uploads per browser session, including legacy `/analyze` uploads.
- `DATA_CLEAN_MAX_OPERATIONS=2`: per-process concurrent POST admission limit.
  Run one worker; use an external queue/rate limit for a scaled deployment.
- `DATA_CLEAN_ORIGINS=http://127.0.0.1:5500,http://localhost:5500`: explicit CORS
  origins; no wildcard credentials. Frontend and API must use the same hostname.
- `DATA_CLEAN_SECURE_COOKIE=0` locally; set `1` with HTTPS. Cookies are HttpOnly,
  SameSite Strict, last 90 days and act as a capability, not verified identity.

SQLite stores artifact ownership separately from plans. Temporary file expiry
never deletes approved plans. Clearing the browser cookie loses access to its
plans; no account recovery exists. No user files or plan databases belong in Git.
This baseline is not a completed public-hosting security assessment.

### Validation

Run `python -m pip install pytest httpx`, then `python -m pytest tests -q`.
`test_semantic_decisions.py` exercises rules, ties, approved mappings, dry runs,
plan compatibility, recalculated statistics and revisions.
`test_file_security.py` exercises cross-session denial, origin checks, expiry,
size/shape limits, deletion and busy responses. UI checks should cover both modes,
private plan save/load/update/delete, explicit approvals, exports and mobile.


Implementation validation (local, synthetic): a 1,000,000-row / 3-column CSV
(~17 MB) completed inspection, analysis, full-data rule checks, a dry run, category
mapping (1,000 values), median filling (100 values), export and download. Original
leading-zero identifiers were preserved. This is a functional capacity check on
one narrow dataset, not a representative benchmark for all files or concurrent
users. Pre-upgrade temporary files have no ownership/expiry registry and are
inaccessible through HTTP; manage those older files separately.


## Stable source records, export checks and review UX

- The engine tracks a separate UInt64 source-record vector (1-based). It is not
  inserted into the user dataset, included in duplicate comparison, or exported
  as an extra data column. Every row filter applies the same mask to this vector.
- Change-log CSV includes `source_row_id`, `operation`, `change_kind` (filled,
  modified, cleared or removed), `method`, explicit old/new null flags and values.
  `row_index` remains for compatibility but now means original zero-based record
  position. Each deleted record has its own entry containing a JSON snapshot at
  deletion time. These are edit/deletion audit records, not an automatic undo API.
- New downloads: `{dataset}_row_mapping.csv` maps output row → original source
  record, and `{dataset}_schema.json` records types, reading options and export
  notes. Both belong to the same session and expiry/delete policy as the output.
- The actual CSV/XLSX artifact is read back before publication. Column names,
  row count and values are verified; a failed check prevents publication. CSV is
  checked against the known typed schema. Other programs can still infer types
  differently; the schema manifest accompanies the file for that reason.
- XLSX writes text as literal text, including formula-looking strings and leading
  zeros. Dates/timezones, decimals and precision-sensitive numbers are preserved
  as text rather than lossy native Excel cells. Empty text and blank cells are
  not reliably distinguishable in Excel; the export report warns to use CSV if
  that distinction matters. Files are written to a private staging name first.
- Review shows pending issue counts and direct access to decisions. In Guided,
  row removal is never selected automatically; explicit unchecked review cards
  offer column/group choices and a reminder to preview the affected records.
  Unresolved items can remain unchanged and do not block downloading results.
- Results distinguish counts of filled/edited/cleared cells and removed rows.
  Cell counts are operation counts, not necessarily distinct cells across a
  multi-action workflow. Cleaned previews label filled/edited cells, show stable
  source records and expose before-values/methods. Unmodified cells retain their
  original-value tooltip. No factual truth is inferred from statistical filling.
- Dry runs retain at most 20 audit examples but report exact operation totals.
  The final full audit logs every change/deletion and can grow large for heavily
  modified files; public resource tuning still needs deployment load testing.

`test_stable_trace_export.py` covers chained deletions followed by edits,
null-versus-empty audit flags, bounded preview totals, colliding user column names,
CSV/Excel round trips (Arabic, leading zeros, dates, large numbers, literal text),
empty exports and protected mapping/schema downloads.

### Clearer workspace controls

- The left navigation stays visible across pages and workflow stages, including
  while scrolling. At widths up to 780px, a fixed top bar opens a full-width menu with
  page links, language and appearance buttons. Escape, an outside tap or a page
  selection closes it. Keyboard focus stays within the open navigation; content
  uses the full screen width. Desktop retains the sidebar.

- Borderless surfaces group the file summary, decisions and treatments. Guided
  decisions start expanded; column meanings, rules and saved plans are under
  optional **More options**. Similar-label approvals stay in the main flow.
- **Select recommended**, **Select safe** and **Deselect all** work in either mode.
  Guided and Expert action selections remain independent. Deselect all also
  clears shared category approvals and recalled action selections for this file.
- A fixed bottom bar keeps the selection count, **Preview changes** and **Apply**
  visible. Its measured height reserves scroll space, including on mobile.
- Selecting a local file shows **File selected — ready to preview**. A separate
  **File uploaded — preview ready** message appears only after inspection succeeds.

Typography uses locally hosted Outfit for page titles and the wordmark, IBM Plex Sans for section headings, and Source Sans 3 for explanations and controls. Outfit is distributed under the included SIL Open Font License (assets/fonts/OFL-Outfit.txt). Rounded actions, short hover transitions, a subtle green home-page glow and a brief page fade take inspiration from the supplied insightsoftware reference. Reduced-motion preferences disable animation and movement; no third-party font request is needed.

Workspace review groups **Review choices** before **Quick changes**. Decision cards keep the treatment and effect visible, with longer explanations under **Why this suggestion?**. Selected badges update with choices; choosing Keep missing/unchanged unticks the action. Optional meanings/checks/plans are under **More options**; original data and findings stay separately accessible. On mobile, navigation moves into a top menu; the workspace mode toolbar stays below the header and the bottom action bar spans the screen.

### Language, appearance and persistent mode controls

- A sticky workspace toolbar keeps Guided/Expert controls accessible during scrolling.
- Sidebar preferences offer English or formal Arabic, with `lang`/`dir` set on the
  document. Arabic places navigation on the right and mirrors interface layout.
  Dataset values, supplied column names, filenames, saved plan names and API
  keys remain unchanged. Technical JSON/error details keep their source text.
- Arabic interface strings, dynamic summaries and planner explanations are in
  `assets/js/i18n.js`. Translation updates text nodes and accessibility labels,
  preserving controls, event listeners and workflow state. Add new user-facing
  strings there; user-content regions must remain explicitly protected.
- Light, Dark and System preferences persist in local browser storage alongside
  language. System follows device appearance changes; unavailable storage falls
  back to session-only choices. No account, server storage or API payload changes.
- IBM Plex Sans Arabic (regular, semibold and bold) is locally hosted with its included SIL Open Font License.
  User-provided content is never sent to an external translation service.

Arabic About/Guide copy uses trusted whole-paragraph templates (data-copy) so punctuation and spacing around emphasized phrases remain natural. English markup is restored exactly when switching back. RTL About/Guide layout and reading rhythm are styled independently; user-data regions remain untouched.

Language and appearance preferences use visible button groups with aria-pressed state and native keyboard activation, replacing dropdowns. The workspace mode labels are Default / Expert (الافتراضي / خبير), under Workspace mode (طريقة الاستخدام).

The Arabic typeface uses IBM Plex Sans Arabic to approximate the supplied university screenshot; it is a visual match choice, not a verified identification of that site’s font. English typefaces are unchanged.


### Refactor stages

1. Completed: request contracts are defined in `src/api_models.py` and re-exported
   by `main_api.py` for compatibility. Fields, defaults and validation limits stay
   unchanged. OpenAPI equality and the existing regression suite verify the move.
2. Next: extract processing orchestration from routes, preserving ownership,
   revision checks, audit cleanup and existing HTTP responses.
3. Next: separate frontend workflow state/rendering from navigation and preferences.
4. Finish: run integration, bilingual responsive UI and performance checks before
   consolidating documentation. Each stage should be validated independently.
