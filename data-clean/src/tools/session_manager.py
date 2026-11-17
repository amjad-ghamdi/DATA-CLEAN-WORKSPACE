"""Browser-session ownership, expiring artifacts and bounded uploads."""
from contextvars import ContextVar
from contextlib import contextmanager
import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path
from fastapi import HTTPException

OWNER = ContextVar("data_clean_owner", default="local")
from .runtime_limits import MAX_UPLOAD_BYTES, MAX_ROWS, MAX_COLUMNS, FILE_TTL


def session_owner(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_token():
    return secrets.token_urlsafe(32)


@contextmanager
def registry(root):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / ".artifacts.sqlite3", timeout=15)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS artifacts (name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL)")
            yield db
    finally:
        db.close()


def register(root, *names):
    with registry(root) as db:
        for name in names:
            db.execute("INSERT INTO artifacts VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires=excluded.expires",
                       (name, OWNER.get(), time.time() + FILE_TTL))


def authorize(root, name):
    if name != Path(name).name or name.startswith("."):
        raise HTTPException(404, "File not found.")
    if OWNER.get() == "local":  # Direct Python calls, never an HTTP identity.
        return
    with registry(root) as db:
        row = db.execute("SELECT owner,expires FROM artifacts WHERE name=?", (name,)).fetchone()
    if not row or row[0] != OWNER.get() or row[1] <= time.time():
        raise HTTPException(404, "File not found or expired.")


def cleanup(root):
    with registry(root) as db:
        expired = db.execute("SELECT name FROM artifacts WHERE expires<=? LIMIT 500", (time.time(),)).fetchall()
        for (name,) in expired:
            path = root / name
            if path.parent == root and path.is_file():
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue  # An externally open Windows file is retried next sweep.
            db.execute("DELETE FROM artifacts WHERE name=?", (name,))


def save_upload(root, filename, stream):
    size = 0
    path = root / filename
    with registry(root) as db:
        active = db.execute("SELECT COUNT(*) FROM artifacts WHERE owner=? AND length(name) IN (36,37) AND substr(name,1,32) NOT GLOB '*[^a-f0-9]*' AND expires>?", (OWNER.get(), time.time())).fetchone()[0]
    if active >= 20:
        raise HTTPException(429, "Remove an old dataset before uploading more than 20 active datasets.")
    try:
        with path.open("wb") as handle:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "File exceeds the configured upload size limit.")
                handle.write(chunk)
        register(root, filename)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def check_shape(frame):
    if frame.height > MAX_ROWS or frame.width > MAX_COLUMNS:
        raise ValueError(f"Dataset exceeds the configured limit: {MAX_ROWS:,} rows / {MAX_COLUMNS} columns.")
