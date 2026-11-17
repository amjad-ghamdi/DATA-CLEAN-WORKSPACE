"""Private reusable decisions in SQLite, outside the repository."""
import json
import sqlite3
import time
from contextlib import contextmanager
from uuid import uuid4


class PlanStore:
    def __init__(self, root):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / ".decisions.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL, updated REAL NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, owner, name, payload, plan_id=None):
        with self.connect() as db:
            if plan_id and not db.execute("SELECT 1 FROM plans WHERE id=? AND owner=?", (plan_id, owner)).fetchone():
                raise ValueError("Plan not found.")
            if not plan_id and db.execute("SELECT COUNT(*) FROM plans WHERE owner=?", (owner,)).fetchone()[0] >= 100:
                raise ValueError("Delete an old plan before saving more than 100 plans.")
            plan_id = plan_id or uuid4().hex
            db.execute("INSERT INTO plans VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,payload=excluded.payload,updated=excluded.updated",
                       (plan_id, owner, name, json.dumps(payload, ensure_ascii=False, allow_nan=False), time.time()))
        return plan_id

    def list(self, owner):
        with self.connect() as db:
            rows = db.execute("SELECT id,name,payload,updated FROM plans WHERE owner=? ORDER BY updated DESC", (owner,)).fetchall()
        return [{"id": r[0], "name": r[1], "payload": json.loads(r[2]), "updated": r[3]} for r in rows]

    def delete(self, owner, plan_id):
        with self.connect() as db:
            return db.execute("DELETE FROM plans WHERE id=? AND owner=?", (plan_id, owner)).rowcount > 0


def compatible_plan(payload, schema, roles):
    problems = []
    for column, expected in payload["schema"].items():
        if column not in schema:
            problems.append(f"Missing column: {column}")
        elif schema[column] != expected:
            problems.append(f"Storage type changed: {column} ({expected} → {schema[column]})")
    for column, saved in payload.get("roles", {}).items():
        current = roles.get(column, {}).get("role", "unknown")
        if current not in {"unknown", saved["role"]} and saved["role"] != "unknown":
            problems.append(f"Column meaning needs confirmation: {column}")
    return {"compatible": not problems, "issues": problems,
            "requires_confirmation": True,
            "note": "Matching names and types do not prove matching business meaning. Review before use; statistics are recomputed."}
