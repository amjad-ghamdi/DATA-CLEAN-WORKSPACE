"""Measure the HTTP analyze -> recommended clean workflow (developer tool)."""

import argparse
import csv
import math
from pathlib import Path
import sys
import time
from uuid import uuid4

import requests


DEFAULT_DATASETS = [f"benchmarks/{size}.csv" for size in ("10k", "100k", "500k", "1m")]
FIELDS = [
    "dataset", "run", "input_bytes", "analyze_seconds", "clean_seconds",
    "total_seconds", "action_count", "rows_before", "rows_after",
    "missing_before", "missing_after", "duplicates_before", "duplicates_after",
    "changes", "validation", "export_mode", "status", "error",
]


def build_recommended_actions(analysis):
    """Match the Guided mode eligibility and action parameter mapping."""
    actions = []
    for step in analysis.get("cleaning_plan", []):
        if (step.get("executable") is False or step.get("risk") == "HIGH IMPACT"
                or step.get("auto_eligible") is not True):
            continue
        name, strategy = step["action"], step.get("strategy")
        if name in {"fill_nulls", "handle_outliers"} and strategy == "keep":
            continue
        action = {"action": name, "column": step["column"]}
        if name == "fill_nulls" and strategy == "contextual_median":
            action.update(
                action="contextual_fill", context_column=step.get("context_column"),
                context_values=step.get("context_values") or [],
                statistic=step.get("statistic") or "median",
                round_result=bool(step.get("round_result", False)),
            )
        elif name == "fill_nulls":
            action["strategy"] = strategy or "keep"
            if strategy == "constant":
                # Guided mode has no constant input; match its empty value conversion.
                dtype = analysis.get("analysis", {}).get("schema", {}).get(step["column"], "")
                action["fill_value"] = 0 if any(t in dtype.lower() for t in ("int", "float", "decimal")) else ""
        elif name == "normalize_casing":
            action["target_case"] = strategy or "title"
        elif name == "handle_outliers":
            action.update(strategy=strategy or "keep", lower_bound=step.get("lower_bound"),
                          upper_bound=step.get("upper_bound"))
        actions.append(action)
    return actions


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", nargs="*", default=DEFAULT_DATASETS)
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=positive_int, default=1)
    parser.add_argument("--timeout", type=positive_float, default=1800,
                        help="HTTP connect/read timeout in seconds (default: 1800)")
    parser.add_argument("--output", type=Path, default=Path("benchmark_results.csv"))
    args = parser.parse_args(argv)
    paths = [Path(p) for p in args.datasets]
    for path in paths:
        if not path.is_file():
            parser.error(f"Dataset not found: {path}. Supply CSV/XLSX/XLS paths explicitly.")
        if path.suffix.lower() not in {".csv", ".xlsx", ".xls"}:
            parser.error(f"Unsupported dataset: {path}")
    output = args.output.resolve()
    if output in [p.resolve() for p in paths]:
        parser.error("Output must not overwrite an input dataset")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    failed = False
    with requests.Session() as session, args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for path in paths:
            for run in range(1, args.runs + 1):
                row = dict.fromkeys(FIELDS, "")
                row.update(dataset=str(path), run=run, status="error")
                try:
                    row["input_bytes"] = path.stat().st_size
                    # Unique names prevent collisions with other uploads and cached reports.
                    upload_name = f"benchmark_{uuid4().hex}{path.suffix.lower()}"
                    with path.open("rb") as source:
                        started = time.perf_counter()
                        try:
                            response = session.post(args.api_base.rstrip("/") + "/analyze",
                                                    files={"file": (upload_name, source)}, timeout=args.timeout)
                        finally:
                            row["analyze_seconds"] = round(time.perf_counter() - started, 6)
                    response.raise_for_status()
                    analysis = response.json()
                    if analysis.get("status") != "success" or not analysis.get("filename"):
                        raise ValueError("Unexpected /analyze response")
                    actions = build_recommended_actions(analysis)
                    row["action_count"] = len(actions)
                    started = time.perf_counter()
                    try:
                        response = session.post(args.api_base.rstrip("/") + "/clean",
                                                json={"filename": analysis["filename"], "actions": actions},
                                                timeout=args.timeout)
                    finally:
                        row["clean_seconds"] = round(time.perf_counter() - started, 6)
                    response.raise_for_status()
                    cleaned = response.json()
                    if cleaned.get("status") != "success":
                        raise ValueError("Unexpected /clean response")
                    validation = cleaned.get("validation", {})
                    for field in ("rows_before", "rows_after", "missing_before", "missing_after",
                                  "duplicates_before", "duplicates_after"):
                        row[field] = validation.get(field, "")
                    row.update(changes=sum(a.get("changed", 0) for a in cleaned.get("applied_actions", [])),
                               validation=validation.get("status", ""),
                               export_mode=cleaned.get("processing", {}).get("export_mode", ""), status="success")
                except (requests.RequestException, OSError, ValueError, KeyError, TypeError) as exc:
                    row["error"] = str(exc)
                    failed = True
                finally:
                    timings = [row[k] for k in ("analyze_seconds", "clean_seconds") if row[k] != ""]
                    row["total_seconds"] = round(sum(timings), 6) if timings else ""
                    writer.writerow(row)
                    handle.flush()
                print(f"{path} run {run}: {row['status']} | analyze={row['analyze_seconds']}s "
                      f"clean={row['clean_seconds']}s {row['error']}")
    print(f"Results: {args.output}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
