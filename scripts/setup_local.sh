#!/bin/sh
# Local setup (PostgreSQL 14+ installed). Run as a user that can run psql as postgres.
set -e
sudo -u postgres psql -tc "select 1 from pg_database where datname='hms'" | grep -q 1 || sudo -u postgres createdb hms
sudo -u postgres psql -d hms -f sql/schema.sql
sudo -u postgres psql -d hms -f sql/002_phase2.sql
echo "Database ready. Next: pip install -r requirements.txt && python -m scripts.create_platform_admin owner 'StrongPass123' && uvicorn hms.app:app --port 8000"
