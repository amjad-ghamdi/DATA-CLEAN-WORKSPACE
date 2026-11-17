"""Bounded semantic suggestions; similarity is never approval to merge values."""
import re
import unicodedata
from collections import defaultdict
import polars as pl

ROLES = {
    "unknown": {"label": "Not specified", "aliases": []},
    "identifier": {"label": "Identifier", "aliases": ["id", "customer id", "uuid", "account number", "postal code", "sku", "معرف", "رقم العميل"]},
    "email": {"label": "Email", "aliases": ["email", "email address", "بريد"]},
    "phone": {"label": "Phone", "aliases": ["phone", "mobile", "telephone", "جوال", "هاتف"]},
    "rating": {"label": "Rating", "aliases": ["score", "rating", "company score", "تقييم"]},
    "salary": {"label": "Salary", "aliases": ["salary", "wage", "راتب"]},
    "date": {"label": "Date", "aliases": ["date", "signup date", "birthdate", "تاريخ"]},
    "category": {"label": "Category", "aliases": ["category", "status", "department", "city", "country", "فئة", "مدينة", "دولة"]},
    "skills": {"label": "Skills / multiple labels", "aliases": ["skills", "مهارات"]},
    "text": {"label": "Free text", "aliases": ["note", "notes", "description", "title", "name", "وصف", "اسم"]},
    "number": {"label": "Number", "aliases": ["amount", "quantity", "count", "مبلغ", "كمية"]},
}


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().replace("_", " ").replace("-", " ").split())


def suggest_roles(frame):
    sample = frame.sample(n=5000, seed=42) if frame.height > 5000 else frame
    result = {}
    for column, dtype in frame.schema.items():
        name = normalized(column)
        matches = [(len(alias), role) for role, spec in ROLES.items() for alias in spec["aliases"]
                   if name == alias or (len(alias) > 2 and alias in name.split())]
        role = max(matches)[1] if matches else "unknown"
        reason = "Column name matches a known alias." if matches else "Meaning could not be established."
        if role == "unknown" and dtype == pl.String:
            values = sample[column].drop_nulls().head(100)
            if len(values) and values.str.contains(r"^[^\s@]+@[^\s@]+\.[^\s@]+$").mean() >= .9:
                role, reason = "email", "Most sampled values have an email-like format."
        if role == "unknown" and dtype.is_numeric():
            role, reason = "number", "Numeric storage type; business meaning is not known."
        result[column] = {"role": role, "source": "suggestion", "confirmed": False,
                          "reason": reason, "sample_rows": sample.height}
    return result


def category_groups(frame, roles, protected=()):
    """Exact full-data counts, but bounded candidates and no quadratic fuzzy search."""
    groups = []
    for column, spec in roles.items():
        if spec["role"] != "category" or column in protected or frame[column].dtype != pl.String:
            continue
        if frame[column].n_unique() > 501:
            continue
        counts = frame[column].drop_nulls().value_counts(sort=True)
        if counts.height > 500:
            continue
        buckets = defaultdict(list)
        for row in counts.to_dicts():
            value = row[column]
            if len(value) <= 200 and normalized(value):
                buckets[normalized(value)].append({"value": value, "count": row["count"]})
        for variants in buckets.values():
            if len(variants) < 2:
                continue
            best = max(v["count"] for v in variants)
            winners = [v["value"] for v in variants if v["count"] == best]
            groups.append({"column": column, "variants": variants,
                           "suggested_target": winners[0] if len(winners) == 1 else None,
                           "needs_review": True, "reason": "Similar spelling only; confirm equivalent meaning."})
            if len(groups) >= 100:
                return groups
    return groups
