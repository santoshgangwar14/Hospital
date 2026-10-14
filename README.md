# City Hospital White-label Hospital Platform — Phase 1 + Phase 2

This package retains the original FastAPI + PostgreSQL hospital platform and Phase 2 clinical/IPD billing modules, with a premium tenant-branded public website served from the existing API routes and a Flutter patient-app UI starter.

## Run the backend and website
Use the setup steps below on a local development machine with PostgreSQL 14+ installed.

```bash
sh scripts/setup_local.sh
python -m pip install -r requirements.txt
python -m scripts.seed_demo
uvicorn hms.app:app --host 127.0.0.1 --port 8000
```

Open:
- `http://127.0.0.1:8000/site/demo-city`
- `http://127.0.0.1:8000/site/demo-sunrise`
- `http://127.0.0.1:8000/app/platform.html`

The public website loads doctor profiles and available slots through the existing public APIs. Its appointment form submits a request for hospital confirmation. It does not take payment or guarantee an appointment.

## Security before use
- Set a unique strong `JWT_SECRET` via environment variable.
- Set `APP_DSN` and `PLATFORM_DSN` to credentials matching the local PostgreSQL roles. Never commit `.env`.
- Change every seeded/demo password and database role password.
- Use HTTPS, backups with a tested restore, monitoring and access policies before any live deployment.

## Flutter patient-app starter
Source: `mobile_app/lib/main.dart`.

Create a Flutter project and replace its `lib/main.dart` with this file, then run:

```bash
flutter pub get
flutter run
```

The app is currently a UI/navigation starter. Patient login, patient-specific APIs, records/reports, secure token storage, push notifications and real appointment submission still require implementation and verification.

## Phase 2 clinical scope
EMR, IPD beds/admissions, nursing, medication administration records, IPD charges/billing, discount approvals and discharge workflows are included in the original backend. Refer to `FINAL_PROJECT_STATUS.md` for scope and verification boundaries.

## Tests
With PostgreSQL configured and test database roles/schema available:

```bash
python -m pytest -q tests
```

Tests are not considered passed merely because the files are present. A clean test run and browser/mobile UAT are required before production.

## Before real use
Set JWT_SECRET, change the database role passwords in sql/schema.sql, put HTTPS in front (Caddy or Nginx), schedule database backups, and have a lawyer review your hospital contracts and data-protection obligations (DPDP Act 2023). Do not claim ABDM, NABH or PM-JAY readiness.
