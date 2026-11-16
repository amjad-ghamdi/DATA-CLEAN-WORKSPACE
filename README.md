<p align="center">
  <img src="data-clean/assets/brand/favicon.svg" width="64" alt="DATA CLEAN grid logo">
</p>

# DATA CLEAN

### Understand your data, review the changes, export with confidence

A bilingual web application for assessing data quality and applying reviewed
cleaning actions to CSV and Excel files. Built with Python, FastAPI and Polars,
with an Arabic/English interface and separate Default and Expert modes.

**[Try the live demo](https://data-clean-workspace.onrender.com/)** ·
**[Run locally](data-clean/README.md#run-locally)** ·
**[Technical guide](data-clean/README.md)**

> **Demo limits:** 5 MiB per file, 25,000 rows and 50 columns, with one heavy
> operation at a time. Free hosting can take about a minute to wake up. Files,
> results and saved plans are temporary and may disappear when the server
> sleeps or restarts. Download outputs you want to keep.

## The problem

Missing values, duplicate records, inconsistent labels and unsafe type conversions
can make datasets difficult to use. Applying every suggested fix automatically
can also remove valid information or create misleading values.

DATA CLEAN makes the findings and treatment choices visible. Users confirm how a
file is read, review proposed changes and preview their effects before execution.
Unresolved values can remain unchanged.

## Workflow

**Upload → confirm file interpretation → assess quality → review decisions →
preview effects → apply selected changes → download data and reports**

## Features

- CSV, XLSX and XLS input with reading preview, delimiter/encoding controls and
  Excel worksheet selection.
- Missing-value and duplicate counts, potential outliers, column profiles and
  transformation suggestions.
- Simple Default mode and detailed Expert mode with independent action selections.
- Reviewed missing-value treatments, whitespace handling, duplicate removal,
  numeric/date conversions and explicitly approved category mappings.
- Column-meaning suggestions and configurable quality checks; private reusable
  plans are checked for compatibility and loaded for review.
- Full-data dry runs, original-record tracking and downloadable change logs.
- Cleaned dataset, validation report, original-row mapping and export/schema notes.
- Arabic/English, RTL layout, light/dark appearance and responsive navigation.

## Engineering highlights

- Polars-based processing, reusable internal snapshots and bounded preview pages.
- Session-scoped artifact access, configurable expiry and upload/shape limits.
- One processing slot in the demo; capacity returns an explicit busy response,
  with manual retry rather than a hidden queue.
- Batched disk-backed audits and actual export read-back checks before publication.
- Protected conversions avoid guessing ambiguous dates or losing identifier zeros.

## Technology

| Layer | Tools |
| --- | --- |
| Interface | HTML, CSS, JavaScript; locally hosted fonts |
| API and contracts | Python, FastAPI, Pydantic, Uvicorn |
| Data processing | Polars, Pandas, NumPy |
| Excel | openpyxl, xlrd |
| Session/plan metadata | SQLite |
| Validation | pytest, API tests and browser acceptance checks |
| Demo hosting | Render |

## Verification and scope

- The refactored application passed **91 Python tests and 12 subtests** locally.
- Browser/API checks covered bilingual decisions, saved plans, dry runs, exports
  and mobile navigation/workflow.
- A local synthetic **1,000,000-row / 3-column** check passed export verification
  and preserved leading-zero identifiers. This is a narrow local test, not a
  guarantee for arbitrary datasets or free hosting.
- The deployed demo was checked with the sample upload-to-download workflow and
  rejection of an oversized file. Demo-profile safety and boundary checks also
  passed locally.

**Validation does not establish factual truth.** Statistical fills are estimates,
format checks do not verify a real-world fact, and potential outliers can be valid.
The recommendation engine uses rules and saved user decisions, not a self-training
LLM. Session cookies are not user accounts. CSV has no native type metadata; use
its schema notes when importing into other tools.

## Repository

Application code, tests and assets live in [`data-clean/`](data-clean/).
See the [technical guide](data-clean/README.md) for local setup, limits, testing,
benchmarks, API routes and deployment configuration.

**بالعربية:** تطبيق لفحص جودة البيانات وتنظيفها بعد مراجعة المستخدم، مع معاينة
أثر التغييرات وتنزيل النتائج والتقارير. رابط العرض نسخة تجريبية محدودة؛ تشغيل
المشروع محليًا يتيح حدودًا أكبر بحسب موارد الجهاز.
