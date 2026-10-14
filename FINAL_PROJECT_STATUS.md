# City Hospital — Final Project Package Status

This bundle combines the original Phase 1 + Phase 2 FastAPI/PostgreSQL platform with a premium, responsive tenant-branded public website and a Flutter patient-app starter.

## Included
- Original Phase 1 + Phase 2 backend, schema, migrations, platform console, staff interface, tests and seed scripts.
- Premium public website served by the existing `/site/{code}` route and `/` tenant route.
- Public website uses existing APIs:
  - `GET /api/public/{code}/doctors`
  - `GET /api/public/{code}/slots`
  - `POST /api/public/{code}/appointments`
- Tenant branding, logo, tagline, contact and primary colour are applied from the tenant configuration.
- Flutter patient-app source at `mobile_app/lib/main.dart`.

## Important limits
- The Flutter app is a UI starter. Patient login, secure token storage, patient records, reports, notifications and appointment API integration are NOT complete.
- Existing SMS delivery is a queue/console stub; no live SMS/WhatsApp provider is configured.
- The public appointment API submits a request for hospital confirmation; it is not a confirmed booking/payment flow.
- Hospital images are externally hosted and require internet. Confirm licensing and replace with hospital-owned/approved photographs before production.
- Doctor cards use the real doctor API. If no active doctor data exists, the site explains that no profiles are published rather than inventing doctors.
- Demo contact details and any seeded credentials must be replaced before real use.
- Production readiness is NOT certified: PostgreSQL integration tests, real-browser tests, Flutter build/device tests, backup restore, HTTPS, secret rotation, and hospital UAT must pass before launch.

## Local run
Follow `README.md`. Recommended sequence:
1. Set up PostgreSQL roles/schema using the supplied setup script.
2. Create a local `.env` from `.env.example` and configure database DSNs and a strong `JWT_SECRET`.
3. Install `requirements.txt`.
4. Seed demo tenants with `python -m scripts.seed_demo` (change demo passwords; never use them in production).
5. Start `uvicorn hms.app:app --host 127.0.0.1 --port 8000`.
6. Open `/site/demo-city`, `/site/demo-sunrise`, and `/app/platform.html`.

## Flutter run
Create a Flutter project, copy `mobile_app/lib/main.dart` to its `lib/main.dart`, then run `flutter pub get` and `flutter run`. No external Flutter packages are required by this starter.

This is a development handoff package, not a claim that all clinical workflows or the mobile app are production-complete.
