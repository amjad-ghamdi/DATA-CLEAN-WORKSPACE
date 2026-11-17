"""Explicit, vectorized rules. No user-supplied code or regular expressions."""
from typing import Literal
import polars as pl
from pydantic import BaseModel, Field, model_validator


class ValidationRule(BaseModel):
    model_config = {"extra": "forbid", "allow_inf_nan": False}
    column: str = Field(min_length=1, max_length=300)
    kind: Literal["required", "unique", "range", "allowed", "email", "date", "length"]
    minimum: float | None = None
    maximum: float | None = None
    values: list[str] = Field(default_factory=list, max_length=500)
    date_format: Literal["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"] = "%Y-%m-%d"

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Minimum must not exceed maximum.")
        if self.kind in {"range", "length"} and self.minimum is None and self.maximum is None:
            raise ValueError("Specify at least one bound.")
        if self.kind == "allowed" and (not self.values or any(len(v) > 1000 for v in self.values)):
            raise ValueError("Provide bounded allowed values.")
        return self


def evaluate_rules(frame, rules):
    findings = []
    row_key = "__rule_row"
    while row_key in frame.columns:
        row_key += "_"
    for raw in rules:
        rule = raw if isinstance(raw, ValidationRule) else ValidationRule(**raw)
        if rule.column not in frame.columns:
            raise ValueError(f"Rule column not found: {rule.column}")
        col = pl.col(rule.column)
        text = col.cast(pl.String)
        present = col.is_not_null()
        if rule.kind == "required":
            bad = col.is_null() | (text.str.strip_chars() == "")
        elif rule.kind == "unique":
            bad = present & col.is_duplicated()
        elif rule.kind == "email":
            bad = present & ~text.str.contains(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
        elif rule.kind == "allowed":
            bad = present & ~text.is_in(rule.values)
        elif rule.kind == "date":
            bad = present & text.str.strptime(pl.Datetime if "%H" in rule.date_format else pl.Date,
                                              format=rule.date_format, strict=False, exact=True).is_null()
        else:
            value = text.str.len_chars().cast(pl.Float64) if rule.kind == "length" else col.cast(pl.Float64, strict=False)
            bad = value.is_null() | value.is_nan() | value.is_infinite()
            if rule.minimum is not None:
                bad = bad | (value < rule.minimum)
            if rule.maximum is not None:
                bad = bad | (value > rule.maximum)
            bad = present & bad
        bad = bad.fill_null(False)
        rows = frame.with_row_index(row_key).select(pl.col(row_key), text.alias("value"), bad.alias("bad")).filter("bad")
        findings.append({"rule": rule.model_dump(), "violations": rows.height,
                         "examples": [{"row": row[row_key] + 1, "value": row["value"]} for row in rows.head(5).to_dicts()]})
    return {"status": "NEEDS_REVIEW" if any(x["violations"] for x in findings) else "PASS",
            "rows_checked": frame.height, "rules_checked": len(findings), "findings": findings,
            "note": "Violation counts can overlap. Passing these rules does not verify real-world truth."}
