"""Explicit local/demo profiles; environment overrides remain available."""
import os

PROFILE = os.getenv('DATA_CLEAN_PROFILE', 'local').lower()
if PROFILE not in {'local', 'demo'}:
    raise ValueError('DATA_CLEAN_PROFILE must be local or demo.')

def limit(name, local, demo):
    value = int(os.getenv(name, str(demo if PROFILE == 'demo' else local)))
    if value < 1:
        raise ValueError(f'{name} must be positive.')
    return value

MAX_UPLOAD_BYTES = limit('DATA_CLEAN_MAX_UPLOAD_MB', 100, 5) * 1024 * 1024
MAX_ROWS = limit('DATA_CLEAN_MAX_ROWS', 2_000_000, 25_000)
MAX_COLUMNS = limit('DATA_CLEAN_MAX_COLUMNS', 500, 50)
MAX_OPERATIONS = limit('DATA_CLEAN_MAX_OPERATIONS', 2, 1)
MAX_EXPANDED_BYTES = limit('DATA_CLEAN_MAX_EXPANDED_MB', 512, 32) * 1024 * 1024
FILE_TTL = limit('DATA_CLEAN_FILE_TTL_HOURS', 24, 1) * 3600
RETRY_AFTER = 3

def public_limits():
    return dict(profile=PROFILE, max_upload_bytes=MAX_UPLOAD_BYTES, max_rows=MAX_ROWS,
                max_columns=MAX_COLUMNS, max_operations=MAX_OPERATIONS,
                file_ttl_seconds=FILE_TTL, queue_enabled=False)
