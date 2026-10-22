# DATA CLEAN V1.1

DATA CLEAN is a risk-aware data-cleaning workspace that profiles CSV datasets, ranks cleaning recommendations, explains them in plain language, applies approved transformations, and validates the result.

## What changed in V1.1

- **Guided mode** is the default UI for non-technical users.
- **Expert mode** exposes strategies, risk labels, technical explanations, previews, lineage, and advanced controls.
- **Apply Recommended Plan** applies only actions that are both recommended and eligible for automatic execution.
- **Recommendation Engine** ranks strategies using datatype, missingness, uniqueness, integrity protection, and semantic/context rules.
- **Context-aware imputation** prevents invalid results such as assigning movie rows a number of seasons or fractional episode counts.
- **Outlier review** supports Keep, Winsorize, or Drop after explicit review; keeping is recommended by default.
- **Explanation layer** converts structured decisions into guided and expert explanations without deciding the cleaning itself.
- **Validation** now reports schema changes and warns about regressions such as numeric columns becoming text.
- **Lineage and exports** remain available for reproducibility.

## Safety model

Each plan step includes:
- Risk: SAFE, REVIEW, or HIGH IMPACT
- Recommended option
- Confidence
- Plain-language reason
- Expert reason
- Whether it is eligible for one-click recommended execution

High-impact steps and ambiguous recommendations are never selected by the one-click plan.

## Architecture

- `src/tools/quality_analyzer.py` — profiling, transformations, missingness, duplicates, outliers
- `src/agent/recommendation_engine.py` — ranked recommendations and context rules
- `src/agent/explanation_agent.py` — user-facing explanations
- `src/skills/safe_cleaning_skill.py` — builds the executable plan
- `src/tools/cleaning_engine.py` — deterministic transformations and lineage
- `src/skills/validation_reporting_skill.py` — before/after validation and warnings
- `main_api.py` — FastAPI API
- `index.html` — Guided / Expert web interface

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

The test suite includes integrity checks, recommendation rules, and context-aware cleaning behavior.
