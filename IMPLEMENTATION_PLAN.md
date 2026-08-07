# Implementation Plan: Cloud Run Deployment + Cloud SQL Migration

Repo: `Pratik-val/gemini-audio-audio-backend` (branch: `main`)
Target: GCP project `nxthyre`, region `asia-south1`
Goal: Move the audio/WebSocket backend off the VM onto Cloud Run with CI/CD, using Cloud SQL (Postgres) as the only datastore.

---

## 1. Current State (verified)

- App entry: `main_v5.py` (FastAPI, WebSocket `/ws/audio`, HTTP `/api/register`, `/api/call/{id}`, `/api/calls`, `/api/telemetry`).
- Datastores today:
  - **MongoDB** (`motor`/`pymongo`) for `calls` via `config/database.py` + `services/call_service.py`.
  - **Cloud SQL Postgres** (`psycopg2`) for `telemetry` via `services/telemetry_service.py` (host `34.93.148.194`, db `nxthyre_dev_db`).
  - **Supabase** (`services/supabase_service.py`) — imported nowhere, dead code.
- Live DB `nxthyre_dev_db` has a `telemetry` table but **no `calls` table**.
- Server currently binds `127.0.0.1:9000` (VM-only). Cloud Run needs `0.0.0.0:$PORT`.

## 2. Decisions (confirmed)

| Topic | Decision |
|-------|----------|
| Hosting | Cloud Run (managed), `asia-south1` |
| Container | Dockerfile built by Cloud Build (no local Docker needed) |
| Datastore | Cloud SQL (Postgres) only — drop Mongo + Supabase |
| DB access | Keep existing public-IP Cloud SQL DSN (no VPC connector) |
| Entry file | `main_v5.py` is the only main file (legacy `main_v1-4` deleted) |
| CI/CD | GitHub Actions on `Pratik-val/gemini-audio-audio-backend@main` |
| Service account | `github-actions-deployer-212@nxthyre.iam.gserviceaccount.com` (WIF) |
| Timeout | Cloud Run 60 min max is sufficient |

---

## 3. Work Items

### Phase 1 — Cleanup (DONE)
Delete: `main_v1.py`, `main_v2.py`, `main_v3.py`, `main_v4.py`, `backup.py`,
`test_server*.py`, `test_gemini.py`, `temp.py`, `scratch/`, `tests/` (broken placeholders),
`audio.mp3`, `audio_logs/`, `audio_output/`, `json1.json`, `json2.json`, `dynamic_data.txt`,
`services/supabase_service.py`.

Keep: `main_v5.py`, `config/`, `services/`, `requirements*.txt`, `sample_client.html`, `.gitignore`.

### Phase 2 — Cloud SQL only (in progress)
1. `config/database.py` → rewrite from Mongo to Postgres helper:
   - `DATABASE_URL` from env; `get_connection()` returning `psycopg2` conn (dict cursor) or `None`.
   - `init_db()` runs `db/schema.sql` idempotently on startup.
2. `db/schema.sql` → new `calls` table (call_id unique, dynamic_data jsonb, transcripts text,
   call_analysis jsonb, timestamps, status). Mirror of the old Mongo doc.
3. `services/call_service.py` → rewrite methods to Postgres SQL while keeping the exact same
   method signatures used by `main_v5.py`:
   `save_call`, `get_call`, `get_all_calls`, `add_transcript_and_timestamp`,
   `update_call_status`, `get_calls_by_interviewer`, `add_transcripts`.
   Keep in-memory fallback when DB unreachable.
4. `main_v5.py`:
   - Remove `connect_to_mongo`/`close_mongo_connection` imports + startup/shutdown hooks.
   - Call `init_db()` on startup (best-effort).
   - Change `__main__` to bind `0.0.0.0` and read `$PORT` (default 8080).

### Phase 3 — Dependencies (DONE)
`requirements.txt` trimmed to runtime-only: `fastapi`, `uvicorn[standard]`, `websockets`,
`pydantic`, `google-genai`, `python-dotenv`, `psycopg2-binary`.
`requirements-dev.txt`: `pytest`, `pytest-asyncio` (kept).

### Phase 4 — Containerize
- `Dockerfile`: python:3.11-slim, install requirements, copy app, expose 8080,
  `CMD ["uvicorn", "main_v5:app", "--host", "0.0.0.0", "--port", "8080"]`.
- `.dockerignore`: `.venv`, `.env`, `.git`, `__pycache__`, tests.
- `cloudbuild.yaml`: build image → push to
  `asia-south1-docker.pkg.dev/nxthyre/nxthyre-server-images/gemini-audio:latest`
  → `gcloud run deploy` (asia-south1, min-instances 1, allow unauthenticated, secrets).

### Phase 5 — Secrets & env
- `telemetry_service.py`: remove hardcoded `DEFAULT_PG_URL` creds; use `DATABASE_URL` env only.
- Secrets (Secret Manager, project `nxthyre`): `GEMINI_API_KEY`, `DATABASE_URL`.
- Cloud Run service `nxthyre-audio` wired with those secrets.

### Phase 6 — CI/CD (GitHub Actions)
`.github/workflows/deploy.yml`:
- trigger: push to `main` (+ workflow_dispatch).
- WIF auth `google-github-actions/auth@v2` → SA `github-actions-deployer-212`.
- `gcloud builds submit` → deploy Cloud Run `nxthyre-audio`.

### Phase 7 — Verify
- Local: `pip install -r requirements.txt`; `python -c "import main_v5"`; `uvicorn main_v5:app` smoke test.
- Post-deploy: `curl https://<service>.run.app/` → WS message; `/api/register` → `/api/telemetry` round-trip; `calls` row lands in Cloud SQL.

---

## 4. Open items (defaulted)
- Keep file name `main_v5.py` (no rename).
- Cloud Run service name: `nxthyre-audio`.
- Tests: old tests deleted; optional new minimal smoke test added later if wanted.
