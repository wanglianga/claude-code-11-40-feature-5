#!/usr/bin/env sh
set -e

python - <<'PY'
import os
import time

import psycopg

url = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://ccdev:ccdev@db:5432/survey_integrity"
)
dsn = url.replace("postgresql+psycopg://", "postgresql://")
for attempt in range(60):
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            print("database is ready")
            break
    except Exception as exc:  # noqa: BLE001
        if attempt == 59:
            raise
        print(f"waiting for database ({attempt + 1}): {exc}")
        time.sleep(2)
PY

python -m app.seed

exec "$@"
